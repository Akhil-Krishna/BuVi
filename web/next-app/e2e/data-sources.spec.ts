import { createHmac } from "node:crypto";
import { test, expect } from "@playwright/test";
import { clearMfaAndSessions, DEMO } from "./reset";
import { signIn } from "./sign-in";

/**
 * Phase B3 DoD, in a real browser: a developer adds a connection, sees it
 * `pending` until a secret is set, tests it, syncs its catalog, and browses
 * tables/columns; an org_admin grants/revokes a specific user's
 * `sql:execute`; a client-role user never reaches the screen. Points at the
 * same real sample-sales-db Postgres instance `scripts/test_analytics_run.py`
 * and `provision_demo_data_source.py` use, under its own unique name so this
 * spec never collides with the connection `chat-and-dashboards.spec.ts`
 * provisions via that script.
 */
test.describe.configure({ mode: "serial" });

const SOURCE_NAME = `e2e-source-${Date.now()}`;

test.beforeAll(() => clearMfaAndSessions(DEMO.developer, DEMO.admin));

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

test("a developer connects, tests, syncs, and browses a data source", async ({ page }) => {
  await signIn(page, "demo-developer");

  // Enrolling TOTP opens the same Section 7.3 step-up window a verify does
  // (mfa_service.py), so the secret submit below needs no separate prompt.
  await page.goto("/account");
  await page.getByRole("button", { name: "Add authenticator app" }).click();
  const secret = (await page.locator("span.font-mono").first().innerText()).trim();
  await page.getByLabel("Verification code").fill(totp(secret));
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("Authenticator app added.")).toBeVisible();

  await page.goto("/data");
  await expect(page.getByRole("heading", { name: "Data Sources" })).toBeVisible();

  await page.getByRole("button", { name: "Add Data Source" }).click();
  await page.getByLabel("Name", { exact: true }).fill(SOURCE_NAME);
  await page.getByLabel("Display label").fill("E2E Postgres");
  await page.getByLabel("Database name").fill("sample_sales");
  await page.getByPlaceholder("public, analytics, ...").fill("sales");
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Save data source" }).click();

  const row = page.getByRole("button", { name: new RegExp(SOURCE_NAME) });
  await expect(row).toBeVisible();
  await expect(row.getByText("pending")).toBeVisible();
  await row.click();

  // A freshly created `pending` connection shows the secret form immediately
  // -- no separate "Set credentials" click needed for the first entry.
  await page.getByLabel("Host").fill("localhost");
  await page.getByLabel("Port").fill("5433");
  await page.getByLabel("Username").fill("buvi_reader");
  await page.getByLabel("Password").fill("dev-reader-password");
  await page.getByLabel("SSL mode").selectOption("disable");
  // Saving credentials alone does not activate the connection -- only a
  // successful test does (data_source_service.py's `set_secret` explicitly
  // keeps it `pending`), so the button still reads "Set credentials" here.
  await page.getByRole("button", { name: "Save credentials" }).click();
  await expect(page.getByRole("button", { name: "Set credentials" })).toBeVisible();

  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByText(/^Connected\. \d+ tables? discovered\.$/)).toBeVisible({
    timeout: 15_000,
  });
  await expect(row.getByText("active")).toBeVisible();

  // The credential is never echoed back: reopening the form shows blanks,
  // not the value just saved (Section 13.1/37).
  await expect(page.getByRole("button", { name: "Update credentials" })).toBeVisible();
  await page.getByRole("button", { name: "Update credentials" }).click();
  await expect(page.getByLabel("Password")).toHaveValue("");
  await page.getByRole("button", { name: "Cancel" }).click();

  await page.getByRole("button", { name: "Sync catalog" }).click();
  await expect(page.getByText(/^Catalog synced: /)).toBeVisible({ timeout: 15_000 });

  await expect(page.getByText("sales.regions")).toBeVisible();
  await page.getByText("sales.regions").click();
  await expect(page.getByRole("columnheader", { name: "Column" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "name", exact: true })).toBeVisible();
});

test("an org_admin grants and revokes a specific user's sql:execute", async ({ page }) => {
  await signIn(page, "demo-admin");
  await page.goto("/data");

  const row = page.getByRole("button", { name: new RegExp(SOURCE_NAME) });
  await expect(row).toBeVisible();
  await row.click();

  await expect(page.getByText("sql:execute grants")).toBeVisible();
  await page
    .getByRole("combobox")
    .selectOption({ label: "developer@demo.example.com (developer)" });
  await page.getByRole("button", { name: "Grant sql:execute" }).click();
  await expect(page.getByRole("cell", { name: "developer@demo.example.com" })).toBeVisible();

  await page.getByRole("button", { name: "Revoke grant" }).click();
  await expect(page.getByRole("cell", { name: "developer@demo.example.com" })).toHaveCount(0);
});

test("a client-role user never reaches the data sources screen", async ({ page }) => {
  await signIn(page, "demo-client");
  await expect(page.getByRole("link", { name: "Data Sources" })).toHaveCount(0);

  await page.goto("/data");
  await expect(page.getByText("Your role does not include access to data sources.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Add Data Source" })).toHaveCount(0);
});
