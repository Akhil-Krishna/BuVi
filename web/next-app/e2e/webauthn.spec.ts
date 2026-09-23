import { test, expect, type Page, type CDPSession } from "@playwright/test";
import { clearMfaAndSessions, DEMO } from "./reset";
import { clickPatiently, signIn, signOut } from "./sign-in";

/**
 * WebAuthn enrollment and verification in a real browser, using Chrome
 * DevTools Protocol virtual authenticators (CDP `WebAuthn` domain) -- a
 * software authenticator the browser treats as real, so `navigator
 * .credentials.create()/get()` run their actual ceremonies and
 * identity-service performs its actual attestation/assertion verification.
 * Nothing here is mocked on either side.
 *
 * This works against the dev server because identity-service's defaults
 * already expect it: `webauthn_rp_id = "localhost"` and `webauthn_origins =
 * ["http://localhost:3000"]` (`Settings.assert_production_safe` flags those
 * as dev values, so production cannot accidentally inherit them).
 *
 * Runs serially and uses its own demo user: enrollment mutates that user's
 * factors, and `scripts/live-flow.sh`'s reset is what puts them back.
 */
const USER = "demo-client";

test.describe.configure({ mode: "serial" });

test.beforeAll(() => clearMfaAndSessions(DEMO.client));

async function addVirtualAuthenticator(page: Page): Promise<CDPSession> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: {
      protocol: "ctap2",
      transport: "internal",
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });
  return cdp;
}

test("a security key can be enrolled, and then satisfies the next sign-in's MFA challenge", async ({
  page,
}) => {
  const cdp = await addVirtualAuthenticator(page);

  // --- Enrollment (Section 6.6: the first factor needs only a session) ---
  await signIn(page, USER);
  await expect(page).toHaveURL("http://localhost:3000/");

  await page.goto("/account");
  await expect(page.getByRole("heading", { name: "Account" })).toBeVisible();
  await expect(page.getByText("No factors enrolled.")).toBeVisible();

  await page.getByRole("button", { name: "Add security key" }).click();
  await expect(page.getByText("Security key added.")).toBeVisible();
  // The factor is now listed, so identity-service accepted the attestation.
  await expect(page.getByRole("cell", { name: "Security key" }).first()).toBeVisible();

  // --- Verification on a fresh session ---
  // Signing out and back in gives a session with mfa_enabled but not yet
  // mfa_verified, which is exactly what the /mfa gate exists for.
  await signOut(page);

  await signIn(page, USER);
  await expect(page).toHaveURL(/\/mfa$/);
  await expect(page.getByRole("heading", { name: "Verify your identity" })).toBeVisible();

  await clickPatiently(page, "Use a security key", async () => !page.url().includes("/mfa"));
  // A satisfied assertion lands the browser back in the app.
  await expect(page).toHaveURL("http://localhost:3000/");
  await expect(page.getByRole("heading", { name: /Signed in as/ })).toBeVisible();

  await cdp.send("WebAuthn.disable");
});

test("a security key the account does not have cannot satisfy the challenge", async ({ page }) => {
  // Enrolled in the previous test; this browser gets a *different* virtual
  // authenticator, so its credentials are unknown to the account.
  const cdp = await addVirtualAuthenticator(page);

  await signIn(page, USER);
  await expect(page).toHaveURL(/\/mfa$/);

  // A plain click on purpose: this test wants the refusal, so it must not be
  // retried away by the patient helper.
  await page.getByRole("button", { name: "Use a security key" }).click();
  // The ceremony cannot produce an assertion for a credential this
  // authenticator never registered, so the app stays on the gate.
  await expect(page).toHaveURL(/\/mfa$/);
  await expect(page.getByText(/cancelled or failed|not accepted/)).toBeVisible();

  await cdp.send("WebAuthn.disable");
});
