import { createHmac } from "node:crypto";
import { execFileSync } from "node:child_process";
import { test, expect, type Page } from "@playwright/test";
import { clearMfaAndSessions, DEMO } from "./reset";
import { signIn } from "./sign-in";

const PGCONTAINER = process.env.PGCONTAINER ?? "buvi-dev-postgres-1";
const DASHBOARD_NAME = "B8 Guest Share Demo";

function psql(sql: string): string {
  return execFileSync(
    "docker",
    ["exec", "-i", PGCONTAINER, "psql", "-U", "postgres", "-d", "agentic_bi", "-tA", "-c", sql],
    { encoding: "utf-8" }
  ).trim();
}

function resetWebhooks(): void {
  psql("DELETE FROM notification.webhook_subscriptions WHERE url = 'https://example.com/webhooks/b8-test';");
}

/** `provision_demo_dashboard.py`'s pin is idempotent -- it only pins once,
 * ever -- so the real `dashboard.tile_pinned` notification it produced is a
 * fixed row, not something a re-run regenerates. Reset just its `read_at` so
 * every suite run starts from the same "unread" state a fresh pin would
 * produce, instead of inheriting whatever a previous run left behind. */
function resetPinNotification(): void {
  psql(`
    UPDATE notification.notifications SET read_at = NULL
    WHERE template_key = 'dashboard.tile_pinned' AND user_id = (
      SELECT id FROM identity.users WHERE email = 'developer@demo.example.com'
    );
  `);
}

/** `SharePanel`'s table lists every link a dashboard has ever had, active or
 * revoked -- a prior run's revoked row would otherwise make `getByText`
 * lookups ambiguous on a later run. */
function resetShareLinks(): void {
  psql(`
    DELETE FROM dashboard.share_links WHERE dashboard_id = (
      SELECT id FROM dashboard.dashboards WHERE name = '${DASHBOARD_NAME}'
    );
  `);
}

test.describe.configure({ mode: "serial" });

test.beforeAll(() => {
  clearMfaAndSessions(DEMO.admin, DEMO.developer, DEMO.client);
  resetWebhooks();
  resetPinNotification();
  resetShareLinks();
});
test.afterAll(() => resetWebhooks());

function totp(secret: string, atMs = Date.now()): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secret.replace(/=+$/, "").toUpperCase()) {
    const i = alphabet.indexOf(c);
    if (i === -1) continue;
    bits += i.toString(2).padStart(5, "0");
  }
  const key = Buffer.from((bits.match(/.{8}/g) ?? []).map((b) => parseInt(b, 2)));
  const counter = Math.floor(atMs / 1000 / 30);
  const msg = Buffer.alloc(8);
  msg.writeUInt32BE(Math.floor(counter / 2 ** 32), 0);
  msg.writeUInt32BE(counter >>> 0, 4);
  const digest = createHmac("sha1", key).update(msg).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const bin =
    ((digest[offset] & 0x7f) << 24) |
    ((digest[offset + 1] & 0xff) << 16) |
    ((digest[offset + 2] & 0xff) << 8) |
    (digest[offset + 3] & 0xff);
  return (bin % 1_000_000).toString().padStart(6, "0");
}

/** Section 7.3's step-up window is 5 minutes: an action right after enrolling
 * shows "Verify"; one a little later may ride the still-fresh window and
 * succeed directly. Check for the prompt instead of assuming either way. */
async function completeStepUpIfPrompted(page: Page, secret: string): Promise<void> {
  const verify = page.getByRole("button", { name: "Verify" });
  if (await verify.isVisible({ timeout: 3_000 }).catch(() => false)) {
    await page.locator('input[autocomplete="one-time-code"]').fill(totp(secret));
    await verify.click();
  }
}

async function enrollTotp(page: Page): Promise<string> {
  await page.goto("/account");
  await page.getByRole("button", { name: "Add authenticator app" }).click();
  const secret = (await page.locator("span.font-mono").first().innerText()).trim();
  await page.getByLabel("Verification code").fill(totp(secret));
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("Authenticator app added.")).toBeVisible();
  return secret;
}

test("org_admin registers, lists, and disables a webhook; the signing secret is shown exactly once", async ({
  page,
}) => {
  await signIn(page, "demo-admin");
  const secret = await enrollTotp(page);

  await page.goto("/webhooks");
  await page.getByRole("button", { name: "Register webhook" }).click();
  await page.getByLabel("Endpoint URL").fill("https://example.com/webhooks/b8-test");
  await page.getByRole("checkbox", { name: "dashboard.tile.pinned" }).check();
  await page.getByRole("button", { name: "Register" }).click();
  await completeStepUpIfPrompted(page, secret);
  await expect(page.getByText("shown once, never again")).toBeVisible({ timeout: 10_000 });
  const secretText = await page.locator("p.font-mono").innerText();
  expect(secretText.length).toBeGreaterThan(10);
  await page.getByRole("button", { name: "Done" }).click();

  const row = page.getByTestId("webhook-row-https://example.com/webhooks/b8-test");
  await expect(row.getByText("active")).toBeVisible();
  await expect(page.getByText(secretText)).toHaveCount(0);
  await page.reload();
  await expect(page.getByText(secretText)).toHaveCount(0); // never shown again, not even after refresh

  await row.getByRole("button", { name: "Disable" }).click();
  await completeStepUpIfPrompted(page, secret);
  await expect(row.getByText("disabled")).toBeVisible({ timeout: 10_000 });
  await expect(row.getByRole("button", { name: "Disable" })).toHaveCount(0);
});

test("a client-role user never reaches webhook administration", async ({ page }) => {
  await signIn(page, "demo-client");
  await expect(page.getByRole("link", { name: "Webhooks" })).toHaveCount(0);
  await page.goto("/webhooks");
  await expect(page.getByText(/does not include access/i)).toBeVisible();
});

test("a real dashboard-pin notification appears, marks read, and another user's id is a real 404", async ({
  page,
}) => {
  // `scripts/provision_demo_dashboard.py` (this file's own fixture) pinned a
  // tile as demo-developer, which produced a genuine `dashboard.tile.pinned`
  // in-app notification for the pinner -- no synthetic data needed.
  await signIn(page, "demo-developer");
  await page.getByRole("button", { name: /Notifications/ }).click();
  const item = page.getByRole("button", { name: /Pinned to your dashboard/ });
  await expect(item).toBeVisible();
  await expect(item).toHaveClass(/bg-bg-subtle/);
  await item.click();
  await expect(item).not.toHaveClass(/bg-bg-subtle/);

  const notificationId = psql(`
    SELECT n.id FROM notification.notifications n
    JOIN identity.users u ON u.id = n.user_id
    WHERE u.email = 'developer@demo.example.com' AND n.template_key = 'dashboard.tile_pinned'
    ORDER BY n.created_at DESC LIMIT 1;
  `);
  expect(notificationId).toMatch(/^[0-9a-f-]{36}$/);

  const admin = await page.context().browser()!.newContext();
  const adminPage = await admin.newPage();
  await signIn(adminPage, "demo-admin");
  const response = await adminPage.request.post(`/api/v1/me/notifications/${notificationId}/read`);
  expect(response.status()).toBe(404);
  await admin.close();
});

test("guest share: a read-only snapshot with zero chrome, then it 404s once revoked", async ({
  page,
  browser,
}) => {
  await signIn(page, "demo-developer");
  const secret = await enrollTotp(page);
  await page.goto("/dashboards");
  await page.getByRole("link", { name: DASHBOARD_NAME }).click();
  await expect(page.getByRole("heading", { name: DASHBOARD_NAME })).toBeVisible();

  await page.getByRole("button", { name: "Create share link" }).click();
  await completeStepUpIfPrompted(page, secret);
  await expect(page.getByText("Copy this link now")).toBeVisible({ timeout: 10_000 });
  const url = await page.locator("p.font-mono").innerText();
  const token = url.split("/share/")[1];
  expect(token).toBeTruthy();
  await page.getByRole("button", { name: "Done" }).click();

  const guest = await browser.newContext();
  const guestPage = await guest.newPage();
  await guestPage.goto(`/share/${token}`);
  await expect(guestPage.getByText("BuVi")).toHaveCount(0);
  await expect(guestPage.getByRole("button", { name: "Sign out" })).toHaveCount(0);
  await expect(guestPage.getByRole("heading", { name: DASHBOARD_NAME })).toBeVisible();
  await expect(guestPage.getByText(/Shared, read-only view/)).toBeVisible();

  await page.getByRole("button", { name: "Revoke link" }).click();
  await expect(page.getByText("Revoked")).toBeVisible({ timeout: 10_000 });

  await guestPage.goto(`/share/${token}`);
  await expect(guestPage.getByText("This link is no longer available.")).toBeVisible();
  await guest.close();
});
