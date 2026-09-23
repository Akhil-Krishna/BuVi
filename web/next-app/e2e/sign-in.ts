import { expect, type Page } from "@playwright/test";

/**
 * Helpers for driving the real Keycloak sign-in, patiently.
 *
 * api-gateway's auth tier is rate limited per IP (Section 16) and a whole
 * suite of browser sign-ins and MFA verifications will reach it -- the same
 * reason `scripts/test_login.py` carries a `patient_api` helper that honours
 * one `Retry-After`. The limit is a control worth keeping, so these wait it
 * out rather than turning it off for tests.
 */
export const DEMO_PASSWORD = "Demo-Passw0rd!23";

// The auth bucket holds ~10 and refills at ~0.2/s (Section 16), and a full
// browser suite spends far more than 10 on sign-ins and MFA verifications.
// So the budget here is deliberately generous: the suite waits the limiter
// out rather than the limiter being loosened for the suite.
const RATE_LIMIT_WAIT_MS = 7_000;
const MAX_ATTEMPTS = 6;

/** True when the app bounced back to its own sign-in screen with a reason,
 * instead of handing off to the IdP. */
function bouncedBack(page: Page): boolean {
  return page.url().includes("error=");
}

async function attemptSignIn(page: Page, username: string): Promise<"ok" | "retry"> {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  await page.getByRole("button", { name: "Continue with SSO" }).click();

  // Either the IdP takes over, or we are back on /login with a reason. Decide
  // *before* touching the login form -- waiting for a field that will never
  // exist just burns the test timeout and hides the real cause.
  await page.waitForURL((url) => url.href.includes("/realms/") || url.href.includes("error="));
  if (bouncedBack(page)) return "retry";

  await page.locator("#username").fill(username);
  await page.locator("#password").fill(DEMO_PASSWORD);
  await page.locator("#kc-login").click();
  await page.waitForURL((url) => !url.href.includes("/realms/"));
  return bouncedBack(page) ? "retry" : "ok";
}

export async function signIn(page: Page, username: string): Promise<void> {
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    if ((await attemptSignIn(page, username)) === "ok") return;
    if (attempt < MAX_ATTEMPTS) await page.waitForTimeout(RATE_LIMIT_WAIT_MS);
  }
  throw new Error(
    `sign-in for ${username} stayed rate limited after ${MAX_ATTEMPTS} attempts (${page.url()})`
  );
}

/** Sign out and wait until the session is actually gone. Clicking the button
 * kicks off a Server Action; navigating before it lands would leave the next
 * sign-in racing a still-valid cookie. */
export async function signOut(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await page.waitForURL(/\/login/);
}

/**
 * Wait for a submission to actually settle into one of its two outcomes.
 *
 * These submit through Server Actions, so reading the page straight after the
 * click sees neither outcome yet -- the earlier version of this helper did
 * exactly that and reported "not rate limited" before the response existed.
 */
async function outcomeOf(
  page: Page,
  succeeded: () => Promise<boolean>
): Promise<"ok" | "limited" | "refused"> {
  const rateLimited = page.getByText(/Too many attempts/);
  for (let waited = 0; waited < 15_000; waited += 200) {
    if (await succeeded()) return "ok";
    if ((await rateLimited.count()) > 0) return "limited";
    await page.waitForTimeout(200);
  }
  return "refused";
}

/** Click a button that hits the auth tier, retrying while the limit refuses it. */
export async function clickPatiently(
  page: Page,
  buttonName: string,
  succeeded: () => Promise<boolean>
): Promise<void> {
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    await page.getByRole("button", { name: buttonName }).click();
    const outcome = await outcomeOf(page, succeeded);
    if (outcome === "ok") return;
    if (outcome === "refused") throw new Error(`"${buttonName}" was refused`);
    await page.waitForTimeout(RATE_LIMIT_WAIT_MS);
  }
  throw new Error(`"${buttonName}" stayed rate limited after ${MAX_ATTEMPTS} attempts`);
}

/**
 * Enter a one-time code and submit it, recomputing the code on every attempt.
 * A TOTP code is only valid for its 30s step, so a retry after a rate-limit
 * wait must not resubmit the code generated before that wait.
 */
export async function submitOneTimeCode(
  page: Page,
  selector: string,
  buttonName: string,
  freshCode: () => string,
  succeeded: () => Promise<boolean>
): Promise<void> {
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    await page.locator(selector).fill(freshCode());
    await page.getByRole("button", { name: buttonName }).click();
    const outcome = await outcomeOf(page, succeeded);
    if (outcome === "ok") return;
    if (outcome === "refused") throw new Error(`"${buttonName}" refused the code`);
    await page.waitForTimeout(RATE_LIMIT_WAIT_MS);
  }
  throw new Error(`"${buttonName}" stayed rate limited after ${MAX_ATTEMPTS} attempts`);
}
