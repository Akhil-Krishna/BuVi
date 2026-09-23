import { test, expect } from "@playwright/test";
import { clearMfaAndSessions, DEMO } from "./reset";
import { signIn } from "./sign-in";

/**
 * Session self-revoke (Section 6.9) in a real browser.
 *
 * Uses `demo-admin`, the one demo user the other two specs leave without an
 * MFA factor -- so sign-in lands straight in the app and this test measures
 * session revocation rather than the MFA gate.
 */
const USER = "demo-admin";

test.beforeAll(() => clearMfaAndSessions(DEMO.admin));

test("revoking another session signs that browser out; revoking your own signs you out", async ({
  page,
  browser,
}) => {
  // A second browser context is a genuinely separate session for the same user.
  const second = await browser.newContext();
  const secondPage = await second.newPage();
  await signIn(secondPage, USER);

  await signIn(page, USER);
  await page.goto("/account");
  await expect(page.getByRole("heading", { name: "Account" })).toBeVisible();

  // Exactly one row is the current session; the other is the second browser.
  await expect(page.getByText("This session", { exact: true })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Revoke", exact: true })).toHaveCount(1);

  // --- Revoke the *other* session ---
  await page.getByRole("button", { name: "Revoke", exact: true }).click();
  await expect(page.getByRole("button", { name: "Revoke", exact: true })).toHaveCount(0);
  // This browser is unaffected -- it still has its own valid session.
  await expect(page.getByRole("heading", { name: "Account" })).toBeVisible();

  // The revoked browser is dead on its next request, server-side.
  await secondPage.goto("/");
  await expect(secondPage).toHaveURL(/\/login$/);
  await second.close();

  // --- Revoke your own current session ---
  await page.getByRole("button", { name: "Revoke this session" }).click();
  // The app must notice on its own: the refresh after revoking re-renders the
  // page server-side, finds no session, and sends the browser to /login. No
  // manual navigation here -- that would hide a failure to react.
  await page.waitForURL(/\/login/);
});
