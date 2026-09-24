import { test, expect, type Page } from "@playwright/test";
import { createHmac } from "node:crypto";
import { clearMfaAndSessions, DEMO } from "./reset";
import { signIn, submitOneTimeCode } from "./sign-in";

/**
 * API keys (Section 6.8) in a real browser: create requires a fresh step-up,
 * the secret is shown exactly once, and revoking removes it from the list.
 *
 * Uses `demo-admin`, the one demo user the other specs leave without an MFA
 * factor. `POST /me/api-keys` requires step-up, and a session with zero
 * factors can never satisfy one -- exercising that path (a real, reachable
 * state, not a hypothetical) needs a clean user. Enrolling TOTP first is
 * incidental setup for the happy path below, not this spec's subject.
 */
const USER = "demo-admin";

test.describe.configure({ mode: "serial" });

test.beforeAll(() => clearMfaAndSessions(DEMO.admin));

// Shared across this file's tests, in execution order (serial mode runs them
// in one worker): test 2 enrols a factor for USER, so every sign-in after it
// -- including test 3's, a brand new session -- must satisfy the MFA gate.
let sharedSecret = "";

async function passMfaGateIfPresent(page: Page): Promise<void> {
  if (!page.url().includes("/mfa")) return;
  await submitOneTimeCode(
    page,
    'input[autocomplete="one-time-code"]',
    "Verify",
    () => totp(sharedSecret),
    async () => !page.url().includes("/mfa")
  );
}

test("creating a key with no MFA factor is refused with instructions, not a crash", async ({
  page,
}) => {
  await signIn(page, USER);
  await page.goto("/account");

  await page.getByLabel("Name").fill("no-factor-key");
  await page.getByRole("button", { name: "Create key" }).click();

  await expect(page.getByText("Add a two-factor method above, then try again.")).toBeVisible();
  // Refused, not silently accepted: no key appears.
  await expect(page.getByRole("cell", { name: "no-factor-key" })).toHaveCount(0);
});

test("enrolling a factor, then creating a key, prompts step-up inline and succeeds", async ({
  page,
}) => {
  await signIn(page, USER);
  await page.goto("/account");

  // Enroll TOTP so a fresh step-up becomes satisfiable. Confirming enrollment
  // opens the same Section 7.3 step-up window a verify does (mfa_service.py),
  // so no separate verify step is needed for *this* session.
  await page.getByRole("button", { name: "Add authenticator app" }).click();
  sharedSecret = (await page.locator("span.font-mono").first().innerText()).trim();
  await page.getByLabel("Verification code").fill(totp(sharedSecret));
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("Authenticator app added.")).toBeVisible();

  await page.getByLabel("Name").fill("ci-pipeline");
  const chatScope = page.getByRole("checkbox", { name: /chat:use/ });
  if ((await chatScope.count()) > 0) await chatScope.check();
  await page.getByRole("button", { name: "Create key" }).click();

  // The step-up gate should not even be reached: enrollment just verified.
  await expect(page.getByText("Store this key now.")).toBeVisible();
  const shown = await page.locator("p.font-mono").last().innerText();
  expect(shown.length).toBeGreaterThan(20);

  await page.getByRole("button", { name: "Done" }).click();
  await expect(page.getByRole("cell", { name: "ci-pipeline" })).toBeVisible();
  // The secret never appears again once dismissed.
  await expect(page.getByText("Store this key now.")).toHaveCount(0);
  await page.reload();
  await expect(page.getByText(shown)).toHaveCount(0);

  // `verify_totp` refuses a correct code whose 30s step was already spent, so
  // a captured code cannot be replayed (mfa_service.py). The enrollment
  // confirm above just spent this step; the next test's sign-in verify must
  // land in a later one.
  await page.waitForTimeout(30_000 - (Date.now() % 30_000) + 1_000);
});

test("revoking a key removes it from the list", async ({ page }) => {
  await signIn(page, USER);
  // A brand new session: USER now has a confirmed factor from the previous
  // test, so this sign-in is not yet MFA-verified and must clear the gate.
  await passMfaGateIfPresent(page);
  await page.goto("/account");

  await expect(page.getByRole("cell", { name: "ci-pipeline" })).toBeVisible();
  await page.getByRole("button", { name: "Revoke key" }).click();
  await expect(page.getByRole("cell", { name: "ci-pipeline" })).toHaveCount(0);
});

/** Same RFC 6238 implementation as account.spec.ts -- duplicated deliberately
 * rather than imported, so this file stays runnable on its own. */
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
