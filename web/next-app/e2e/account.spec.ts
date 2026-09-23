import { test, expect, type Page } from "@playwright/test";
import { createHmac } from "node:crypto";
import { clearMfaAndSessions, DEMO } from "./reset";
import { signIn, signOut, submitOneTimeCode } from "./sign-in";

/**
 * Account settings: TOTP enrollment (Section 6.6) and session self-revoke
 * (Section 6.9), in a real browser.
 *
 * Uses `demo-developer` rather than `demo-client` on purpose: `webauthn.spec`
 * enrolls a factor for the client, and Section 6.6 requires a fresh step-up
 * before a *second* factor -- two specs enrolling onto one account would make
 * each other's outcome depend on run order.
 */
const USER = "demo-developer";

test.describe.configure({ mode: "serial" });

test.beforeAll(() => clearMfaAndSessions(DEMO.developer));

/** RFC 6238 TOTP (SHA-1, 6 digits, 30s), matching pyotp's defaults on the
 * server. Hand-rolled to avoid a dependency for test-only code: the server
 * validates independently, so a mistake here fails the test rather than
 * weakening anything. */
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

/**
 * Wait until the current 30s TOTP step ends.
 *
 * `verify_totp` refuses a correct code whose step was already spent, so a
 * captured code cannot be replayed (mfa_service.py). Enrolling and then
 * verifying at the next sign-in inside the same step would hit exactly that
 * protection -- the wait is the test respecting a real control, not a
 * workaround for a flaky one.
 */
async function waitForNextTotpStep(page: Page): Promise<void> {
  await page.waitForTimeout(30_000 - (Date.now() % 30_000) + 1_000);
}

test("an authenticator app can be enrolled and then satisfies the next sign-in", async ({
  page,
}) => {
  await signIn(page, USER);
  await page.goto("/account");

  await page.getByRole("button", { name: "Add authenticator app" }).click();
  await expect(page.getByAltText("TOTP enrollment QR code")).toBeVisible();

  // The manual-entry key is the same secret the QR encodes -- use it to
  // produce a real code, exactly as an authenticator app would.
  const secret = (await page.locator("span.font-mono").first().innerText()).trim();
  await submitOneTimeCode(
    page,
    'input[aria-label="Verification code"]',
    "Confirm",
    () => totp(secret),
    async () => (await page.getByText("Authenticator app added.").count()) > 0
  );

  await expect(page.getByText("Authenticator app added.")).toBeVisible();
  await expect(page.getByRole("cell", { name: "Authenticator app" }).first()).toBeVisible();

  // A fresh session must now pass the MFA gate before reaching the app.
  await waitForNextTotpStep(page);
  await signOut(page);
  await signIn(page, USER);
  await expect(page).toHaveURL(/\/mfa$/);

  await submitOneTimeCode(
    page,
    'input[autocomplete="one-time-code"]',
    "Verify",
    () => totp(secret),
    async () => !page.url().includes("/mfa")
  );
  await expect(page).toHaveURL("http://localhost:3000/");
});

test("a wrong code is refused and leaves the gate closed", async ({ page }) => {
  await signIn(page, USER);
  await expect(page).toHaveURL(/\/mfa$/);

  await page.locator('input[autocomplete="one-time-code"]').fill("000000");
  // A plain click on purpose: this test wants the refusal, so it must not be
  // retried away by the patient helper.
  await page.getByRole("button", { name: "Verify" }).click();

  await expect(page.getByText(/not accepted/)).toBeVisible();
  await expect(page).toHaveURL(/\/mfa$/);
});

test("an unverified session cannot reach account settings by URL", async ({ page }) => {
  await signIn(page, USER);
  await expect(page).toHaveURL(/\/mfa$/);

  await page.goto("/account");
  // The page re-checks the session itself rather than trusting proxy.ts,
  // which only knows a cookie exists (Section 4.2, 7.4).
  await expect(page).toHaveURL(/\/mfa$/);
});
