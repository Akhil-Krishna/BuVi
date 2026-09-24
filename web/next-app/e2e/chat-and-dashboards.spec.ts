import { execFileSync } from "node:child_process";
import { test, expect } from "@playwright/test";
import { createHmac } from "node:crypto";
import { clearMfaAndSessions, DEMO } from "./reset";
import { signIn } from "./sign-in";

/**
 * Section 32's first vertical slice, in a real browser: message -> SSE
 * execution trace -> chart -> pin to dashboard -> the dashboard grid and
 * detail page both show it. Section 11's cancellation fix is exercised here
 * too -- a cancelled run must read as "cancelled", not as a failure, off the
 * typed `run.cancelled` status alone.
 */
test.describe.configure({ mode: "serial" });

test.beforeAll(() => {
  clearMfaAndSessions(DEMO.client, DEMO.developer);
  // A chat run needs an active data source; Phase B3 (the UI that would
  // otherwise connect one) is not built yet, and `reset_demo_state` itself
  // does not seed one (`live-flow.sh` deletes any existing `sample-sales-db`
  // row rather than recreating it). Idempotent -- a no-op once it exists.
  execFileSync("uv", ["run", "--package", "identity-service", "python",
    "scripts/provision_demo_data_source.py"], { cwd: "../..", stdio: "inherit" });
});

/** Opens the pin dialog and always picks "New dashboard" -- a prior run (or
 * an earlier test in this file) may have left the picker non-empty, in which
 * case an existing dashboard is pre-selected and the name field is hidden. */
async function pinToNewDashboard(page: import("@playwright/test").Page, name: string) {
  await page.getByRole("button", { name: "Pin to Dashboard" }).click();
  const newDashboardRadio = page.getByRole("radio", { name: "New dashboard" });
  if ((await newDashboardRadio.count()) > 0) await newDashboardRadio.check();
  await page.getByPlaceholder("Dashboard name").fill(name);
  await page.getByRole("button", { name: "Pin", exact: true }).click();
  await expect(page.getByText("Pinned to dashboard.")).toBeVisible();
}

test("a client sends a message, watches it run, and pins the chart to a new dashboard", async ({
  page,
}) => {
  await signIn(page, "demo-client");
  await page.goto("/chat");
  await expect(page.getByRole("heading", { name: "Chat" })).toBeVisible();

  const prompt = "Create a sales dashboard for Q2";
  await page.getByPlaceholder("Ask a follow-up question or specify a slice...").fill(prompt);
  await page.getByRole("button", { name: "Send" }).click();

  // Not `getByText` -- the composer's own textarea still holds this exact
  // string until the pending Server Action clears it, which would otherwise
  // make this a strict-mode ambiguity.
  await expect(page.locator("p.font-medium", { hasText: prompt })).toBeVisible();
  await expect(page.getByText("Execution trace")).toBeVisible();

  // The full CrewAI Flow, through the scripted provider, over real SSE.
  await expect(page.getByRole("button", { name: "Pin to Dashboard" })).toBeVisible({
    timeout: 60_000,
  });
  // Cancel button disappears once the run reaches a terminal state.
  await expect(page.getByRole("button", { name: "Cancel run" })).toHaveCount(0);

  const dashboardName = `Q2 Sales ${Date.now()}`;
  await pinToNewDashboard(page, dashboardName);

  await page.goto("/dashboards");
  await expect(page.getByRole("link", { name: dashboardName })).toBeVisible();

  await page.getByRole("link", { name: dashboardName }).click();
  await expect(page.getByRole("heading", { name: dashboardName })).toBeVisible();
  await expect(page.getByText("This chart could not be rendered.")).toHaveCount(0);
  await expect(page.getByText("This artifact is no longer available.")).toHaveCount(0);
});

test("a user-cancelled run reads as cancelled, not failed", async ({ page }) => {
  await signIn(page, "demo-client");
  await page.goto("/chat");

  await page
    .getByPlaceholder("Ask a follow-up question or specify a slice...")
    .fill("Show revenue by month");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByRole("button", { name: "Cancel run" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel run" }).click();

  await expect(page.getByText("Run cancelled.")).toBeVisible({ timeout: 30_000 });
  // Section 11: a real failure and a cancellation must never render the same
  // way -- this run must not also show the generic failure paragraph.
  await expect(page.getByText("The run failed.")).toHaveCount(0);
});

test("dashboard:share is step-up protected and the token is shown exactly once", async ({
  page,
}) => {
  await signIn(page, "demo-developer");
  await page.goto("/chat");

  await page
    .getByPlaceholder("Ask a follow-up question or specify a slice...")
    .fill("Show order counts by region");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("button", { name: "Pin to Dashboard" })).toBeVisible({
    timeout: 60_000,
  });

  const dashboardName = `Regional Orders ${Date.now()}`;
  await pinToNewDashboard(page, dashboardName);

  await page.goto("/dashboards");
  await page.getByRole("link", { name: dashboardName }).click();
  await page.waitForURL(/\/dashboards\/[^/]+$/);
  const dashboardUrl = page.url();
  await expect(page.getByRole("heading", { name: "Share" })).toBeVisible();

  // A brand-new session for demo-developer has no MFA factor yet: creating a
  // share link must refuse with instructions, not a bare error (the same
  // pattern api-keys.spec.ts proves for API key creation).
  await page.getByRole("button", { name: "Create share link" }).click();
  await expect(page.getByText("Add a two-factor method")).toBeVisible();

  // Enrolling TOTP opens the same Section 7.3 step-up window a verify does
  // (mfa_service.py), so the retry below needs no separate verify step.
  await page.goto("/account");
  await page.getByRole("button", { name: "Add authenticator app" }).click();
  const secret = (await page.locator("span.font-mono").first().innerText()).trim();
  await page.getByLabel("Verification code").fill(totp(secret));
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("Authenticator app added.")).toBeVisible();

  await page.goto(dashboardUrl);
  await page.getByRole("button", { name: "Create share link" }).click();
  await expect(page.getByText("Copy this link now")).toBeVisible();
  const url = await page.locator("p.font-mono").innerText();
  expect(url).toMatch(/^https?:\/\//);

  await page.getByRole("button", { name: "Done" }).click();
  await expect(page.getByText("Active")).toBeVisible();

  await page.getByRole("button", { name: "Revoke link" }).click();
  await expect(page.getByText("Revoked")).toBeVisible();
  await expect(page.getByRole("button", { name: "Revoke link" })).toHaveCount(0);
});

/** Same RFC 6238 implementation as account.spec.ts/api-keys.spec.ts --
 * duplicated deliberately so this file stays runnable on its own. */
function totp(base32Secret: string, atMs = Date.now()): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of base32Secret.replace(/=+$/, "").toUpperCase()) {
    const index = alphabet.indexOf(char);
    if (index === -1) continue;
    bits += index.toString(2).padStart(5, "0");
  }
  const key = Buffer.from((bits.match(/.{8}/g) ?? []).map((byte) => parseInt(byte, 2)));
  const counter = Math.floor(atMs / 1000 / 30);
  const message = Buffer.alloc(8);
  message.writeUInt32BE(Math.floor(counter / 2 ** 32), 0);
  message.writeUInt32BE(counter >>> 0, 4);
  const digest = createHmac("sha1", key).update(message).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const binary =
    ((digest[offset] & 0x7f) << 24) |
    ((digest[offset + 1] & 0xff) << 16) |
    ((digest[offset + 2] & 0xff) << 8) |
    (digest[offset + 3] & 0xff);
  return (binary % 1_000_000).toString().padStart(6, "0");
}
