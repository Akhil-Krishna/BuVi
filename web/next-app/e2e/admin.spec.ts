import { createHmac } from "node:crypto";
import { execFileSync } from "node:child_process";
import { test, expect, type Page } from "@playwright/test";
import { clearMfaAndSessions, DEMO } from "./reset";
import { signIn } from "./sign-in";

const PGCONTAINER = process.env.PGCONTAINER ?? "buvi-dev-postgres-1";

function psql(sql: string): void {
  execFileSync(
    "docker",
    ["exec", "-i", PGCONTAINER, "psql", "-U", "postgres", "-d", "agentic_bi", "-c", sql],
    { stdio: "pipe" }
  );
}

/** No demo Keycloak identity exists anywhere in this codebase for `auditor`
 * or `billing_admin` (Track A's own test suite only ever exercises those two
 * roles by constructing a `Principal` directly, never through a real login) --
 * standing one up would mean touching frozen Track A bootstrap infra for a
 * single UI nuance. Instead, test 3 below reuses this suite's own real
 * role-grant action (Section 9: `PATCH /admin/users/{id}/roles`) to add
 * `auditor` to `demo-developer` for the duration of this file, proving the
 * real read-only invariant with a real permission set, then reverts it here
 * so other specs' `demo-developer` session is unaffected. */
function resetAuditorGrant(): void {
  psql(`
    DELETE FROM identity.user_roles WHERE user_id = (
      SELECT id FROM identity.users WHERE email = 'developer@demo.example.com'
    ) AND role_id = (
      SELECT id FROM identity.roles WHERE key = 'auditor'
        AND tenant_id = (SELECT tenant_id FROM identity.users WHERE email = 'developer@demo.example.com')
    );
  `);
}

function resetInvitations(): void {
  psql("DELETE FROM identity.invitations WHERE email LIKE 'b7-invite-%';");
}

test.describe.configure({ mode: "serial" });

test.beforeAll(() => {
  clearMfaAndSessions(DEMO.admin, DEMO.developer, DEMO.client);
  resetAuditorGrant();
  resetInvitations();
});
test.afterAll(() => {
  resetAuditorGrant();
  resetInvitations();
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

const INVITE_EMAIL = `b7-invite-${Date.now()}@demo.example.com`;

/** Section 7.3's step-up window is 5 minutes: once this test's own TOTP
 * enrollment establishes a fresh one, every following step-up-gated action in
 * this same run rides it and succeeds with no new prompt -- only the first
 * action after enrollment (or one after the window lapses) actually shows
 * "Verify". Assuming a prompt always appears is exactly the wrong assumption
 * `mcp.spec.ts` made and had to fix; this checks for it instead of assuming. */
async function completeStepUpIfPrompted(page: Page, secret: string): Promise<void> {
  const verify = page.getByRole("button", { name: "Verify" });
  if (await verify.isVisible({ timeout: 3_000 }).catch(() => false)) {
    await page.locator('input[autocomplete="one-time-code"]').fill(totp(secret));
    await verify.click();
  }
}

test("org_admin: invite, roles, sessions, MFA reset, policies, audit, billing", async ({ page }) => {
  await signIn(page, "demo-admin");

  await page.goto("/account");
  await page.getByRole("button", { name: "Add authenticator app" }).click();
  const secret = (await page.locator("span.font-mono").first().innerText()).trim();
  await page.getByLabel("Verification code").fill(totp(secret));
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("Authenticator app added.")).toBeVisible();

  // --- Users: invite (step-up) ---
  await page.goto("/users");
  await page.getByRole("button", { name: "Invite user" }).click();
  await page.getByLabel("Email").fill(INVITE_EMAIL);
  await page.getByRole("button", { name: "Send invitation" }).click();
  await completeStepUpIfPrompted(page, secret);
  await expect(page.getByText(INVITE_EMAIL)).toBeVisible({ timeout: 10_000 });

  // --- Last org_admin refusal: demote/deactivate self is refused, not silently allowed ---
  const adminPanel = page.getByTestId("user-row-admin@demo.example.com");
  await adminPanel.getByRole("button", { name: /admin@demo\.example\.com/ }).click();
  await adminPanel.getByRole("checkbox", { name: "org_admin" }).uncheck();
  await adminPanel.getByRole("button", { name: "Save roles" }).click();
  await completeStepUpIfPrompted(page, secret);
  await expect(adminPanel.getByText(/cannot remove the organization administrator role/i)).toBeVisible({
    timeout: 10_000,
  });
  await adminPanel.getByRole("button", { name: "Deactivate" }).click();
  await completeStepUpIfPrompted(page, secret);
  await expect(adminPanel.getByText(/cannot delete your own account/i)).toBeVisible({
    timeout: 10_000,
  });

  // --- Role grant on another real user (step-up), sessions revoke, MFA reset ---
  const devPanel = page.getByTestId("user-row-developer@demo.example.com");
  await devPanel.getByRole("button", { name: /developer@demo\.example\.com/ }).click();
  await devPanel.getByRole("checkbox", { name: "auditor" }).check();
  await devPanel.getByRole("button", { name: "Save roles" }).click();
  await completeStepUpIfPrompted(page, secret);
  await expect(devPanel.getByRole("checkbox", { name: "auditor" })).toBeChecked({ timeout: 10_000 });

  await devPanel.getByRole("button", { name: "Revoke sessions" }).click();
  await completeStepUpIfPrompted(page, secret);
  await expect(devPanel.getByText(/session\(s\) revoked/)).toBeVisible({ timeout: 10_000 });

  await devPanel.getByRole("button", { name: "Reset MFA" }).click();
  await completeStepUpIfPrompted(page, secret);
  await expect(devPanel.getByText(/factor\(s\) and \d+ session\(s\) revoked/)).toBeVisible({
    timeout: 10_000,
  });

  // --- Policies (step-up), persists across a refresh ---
  await page.goto("/policies");
  const shareCheckbox = page.getByRole("checkbox", {
    name: "Clients can create dashboard share links",
  });
  const wasChecked = await shareCheckbox.isChecked();
  await shareCheckbox.setChecked(!wasChecked);
  await page.getByRole("button", { name: "Save changes" }).click();
  await completeStepUpIfPrompted(page, secret);
  await expect(shareCheckbox).toBeChecked({ checked: !wasChecked, timeout: 10_000 });
  await page.reload();
  await expect(
    page.getByRole("checkbox", { name: "Clients can create dashboard share links" })
  ).toBeChecked({ checked: !wasChecked, timeout: 10_000 });
  // Restore, so this test is idempotent across runs.
  await page
    .getByRole("checkbox", { name: "Clients can create dashboard share links" })
    .setChecked(wasChecked);
  await page.getByRole("button", { name: "Save changes" }).click();
  await completeStepUpIfPrompted(page, secret);

  // --- Audit: the role change above produced a real row ---
  await page.goto("/audit");
  await expect(page.getByText("user.role_changed").first()).toBeVisible({ timeout: 10_000 });

  // --- Billing: real numbers from analytics-orchestrator ---
  await page.goto("/billing");
  await expect(page.getByText("LLM tokens (today)")).toBeVisible();
  await expect(page.getByText("Seats")).toBeVisible();
});

test("a client-role user never reaches the admin console", async ({ page }) => {
  await signIn(page, "demo-client");
  for (const name of ["Users", "Policies", "Audit", "Billing"]) {
    await expect(page.getByRole("link", { name })).toHaveCount(0);
  }
  for (const path of ["/users", "/policies", "/audit", "/billing"]) {
    await page.goto(path);
    await expect(page.getByText(/does not include access/i)).toBeVisible();
  }
});

test("a session with audit:read but no admin permission sees read-only audit, never a mutating page", async ({
  page,
}) => {
  await signIn(page, "demo-developer");
  await expect(page.getByRole("link", { name: "Audit" })).toBeVisible();
  await page.goto("/audit");
  await expect(page.getByRole("heading", { name: "Audit Log" })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Filter$/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /save|invite|deactivate/i })).toHaveCount(0);

  await expect(page.getByRole("link", { name: "Users" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Policies" })).toHaveCount(0);
  await page.goto("/users");
  await expect(page.getByText(/does not include access/i)).toBeVisible();
  await page.goto("/policies");
  await expect(page.getByText(/does not include access/i)).toBeVisible();
});
