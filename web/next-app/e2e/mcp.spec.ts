import { createHmac } from "node:crypto";
import { execFileSync } from "node:child_process";
import { test, expect } from "@playwright/test";
import { clearMfaAndSessions, DEMO } from "./reset";
import { signIn } from "./sign-in";

/** Leftover servers from earlier runs stay `approved`, whose status text
 * substring-matches the "Approve" button's accessible name -- same class of
 * bug B5's stale-metric reset fixed. */
function resetMcpFixtures(): void {
  execFileSync(
    "docker",
    [
      "exec",
      "-i",
      process.env.PGCONTAINER ?? "buvi-dev-postgres-1",
      "psql",
      "-U",
      "postgres",
      "-d",
      "agentic_bi",
      "-c",
      "DELETE FROM mcp.servers WHERE name LIKE 'Sample MCP %';",
    ],
    { stdio: "pipe" }
  );
}

/** Section 7.3's step-up window is 5 minutes; forces it to have lapsed so the
 * write tool's invoke actually exercises "fresh step-up required" instead of
 * riding the still-fresh step-up from this test's own earlier TOTP enrollment. */
function expireStepUp(email: string): void {
  const sql = `
    UPDATE identity.sessions SET mfa_verified_at = now() - interval '6 minutes'
    WHERE user_id = (SELECT id FROM identity.users WHERE email = '${email.replace(/'/g, "''")}');
  `;
  execFileSync(
    "docker",
    [
      "exec",
      "-i",
      process.env.PGCONTAINER ?? "buvi-dev-postgres-1",
      "psql",
      "-U",
      "postgres",
      "-d",
      "agentic_bi",
      "-c",
      sql,
    ],
    { stdio: "pipe" }
  );
}

/**
 * Phase B6 DoD, in a real browser, against the real sample MCP server
 * (`platform_testing.mcp`, :8765) `scripts/test_mcp.py` also uses: register
 * -> pending_approval -> approve (org_admin, step-up) -> grant -> invoke a
 * read tool -> a write tool needs step-up at invoke too -> disable makes
 * tools refused even from the same open page.
 */
test.describe.configure({ mode: "serial" });

const SERVER_NAME = `Sample MCP ${Date.now()}`;

test.beforeAll(() => {
  clearMfaAndSessions(DEMO.admin, DEMO.client);
  resetMcpFixtures();
});

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

test("register, approve, grant, invoke a read tool, step-up a write tool, then disable", async ({
  page,
}) => {
  await signIn(page, "demo-admin");

  // Enrolling opens the same step-up window a verify does -- needed below
  // for both `approve` and the write tool's invoke.
  await page.goto("/account");
  await page.getByRole("button", { name: "Add authenticator app" }).click();
  const secret = (await page.locator("span.font-mono").first().innerText()).trim();
  await page.getByLabel("Verification code").fill(totp(secret));
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("Authenticator app added.")).toBeVisible();

  await page.goto("/mcp");
  await page.getByRole("button", { name: "Register server" }).click();
  await page.getByLabel("Name").fill(SERVER_NAME);
  await page.getByLabel("Endpoint URL").fill("http://localhost:8765/mcp");
  const rows = page.locator('input[placeholder="tool_name"]');
  await rows.nth(0).fill("search_docs");
  await page.locator("select").nth(0).selectOption("read_metadata");
  await page.getByRole("button", { name: "+ Add tool" }).click();
  await rows.nth(1).fill("delete_order");
  await page.locator("select").nth(1).selectOption("write");
  await page.getByRole("button", { name: "Register", exact: true }).click();

  const serverRow = page.getByTestId(`server-row-${SERVER_NAME}`);
  const serverButton = serverRow.getByRole("button", { name: new RegExp(SERVER_NAME) });
  await expect(serverButton.getByText("pending_approval")).toBeVisible();
  await serverButton.click();

  await serverRow.getByRole("button", { name: "Approve" }).click();
  await expect(serverButton.getByText("approved")).toBeVisible();

  // Grant + invoke the read tool.
  const searchRow = page.getByTestId("tool-row-search_docs");
  await searchRow.getByRole("combobox").selectOption("role:org_admin");
  await searchRow.getByRole("button", { name: "Grant" }).click();
  await expect(searchRow.getByRole("listitem").getByText("role: org_admin")).toBeVisible();
  await searchRow.locator("textarea").fill('{"query":"test"}');
  await searchRow.getByRole("button", { name: "Invoke" }).click();
  await expect(searchRow.getByText(/documents mention/i)).toBeVisible({ timeout: 10_000 });

  // Grant + invoke the write tool: needs a fresh step-up at invoke itself,
  // not just to have been granted.
  const deleteRow = page.getByTestId("tool-row-delete_order");
  await deleteRow.getByRole("combobox").selectOption("role:org_admin");
  await deleteRow.getByRole("button", { name: "Grant" }).click();
  expireStepUp(DEMO.admin);
  await deleteRow.locator("textarea").fill('{"order_id":1}');
  await deleteRow.getByRole("button", { name: "Invoke" }).click();
  await expect(deleteRow.getByRole("button", { name: "Verify" })).toBeVisible();
  await deleteRow.locator('input[autocomplete="one-time-code"]').fill(totp(secret));
  await deleteRow.getByRole("button", { name: "Verify" }).click();
  await expect(deleteRow.getByText(/OK|deleted/i)).toBeVisible({ timeout: 10_000 });

  // Disable: tools refused from this same, still-open page.
  await page.getByRole("button", { name: "Disable" }).click();
  await expect(serverButton.getByText("disabled")).toBeVisible();
  await searchRow.locator("textarea").fill('{"query":"test"}');
  await searchRow.getByRole("button", { name: "Invoke" }).click();
  await expect(searchRow.getByText("This MCP server is not approved.")).toBeVisible();
});

test("a client-role user never reaches MCP governance", async ({ page }) => {
  await signIn(page, "demo-client");
  await expect(page.getByRole("link", { name: "MCP" })).toHaveCount(0);

  await page.goto("/mcp");
  await expect(
    page.getByText("Your role does not include access to MCP governance.")
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Register server" })).toHaveCount(0);
});
