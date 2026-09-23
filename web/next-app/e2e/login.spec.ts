import { test, expect, type Page } from "@playwright/test";
import { clearMfaAndSessions, DEMO } from "./reset";
import { signIn, signOut } from "./sign-in";

/**
 * Phase B1 DoD: the real Authorization Code + PKCE flow, through an actual
 * browser -- Keycloak's own login form, a real cross-origin navigation, and
 * cookies inspected at the browser layer (Playwright's cookie jar), not a
 * client-side convenience object. Demo users/passwords match
 * `scripts/test_login.py` (Phase A1's own DoD script) exactly.
 */

test.beforeAll(() => clearMfaAndSessions(DEMO.client, DEMO.developer));

/** Signs in, first proving the browser really is sent to Keycloak's authorize
 * endpoint -- the one assertion specific to this spec, which exists to prove
 * the OIDC redirect itself rather than just its outcome. */
async function signInThroughKeycloak(page: Page, username: string) {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  await page.getByRole("button", { name: "Continue with SSO" }).click();
  await expect(page).toHaveURL(/\/realms\/buvi\/protocol\/openid-connect\/auth/);
  await page.goBack();
  await signIn(page, username);
}

test("a client-role user completes the full OIDC flow and sees the client nav", async ({
  page,
  context,
}) => {
  await signInThroughKeycloak(page, "demo-client");

  // The callback route must land the browser on a real page, not the 204
  // identity-service itself returns.
  await expect(page).toHaveURL("http://localhost:3000/");
  await expect(page.getByRole("heading", { name: /Signed in as/ })).toBeVisible();

  // Client nav: Chat + Dashboards, never the developer/admin items.
  await expect(page.getByRole("link", { name: "Chat" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Dashboards" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Users" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "SQL Lab" })).toHaveCount(0);

  const cookies = await context.cookies();
  const session = cookies.find((c) => c.name === "buvi_session");
  expect(session, "buvi_session cookie must be set").toBeTruthy();
  expect(session!.httpOnly).toBe(true);
  expect(session!.sameSite).toBe("Lax");
  // Secure is environment-conditional (ADR 0018): off over plain HTTP in dev,
  // on in production -- a `Secure` cookie set over HTTP would be silently
  // dropped by the browser, breaking local development entirely.
  expect(session!.secure).toBe(false);
  expect(session!.value).not.toMatch(/^[0-9a-f-]{36}$/); // opaque, not a raw session row id
  // No JWT/refresh-token shape ever reaches the browser (Section 6.2).
  expect(session!.value.split(".").length).not.toBe(3);

  await signOut(page);
  const afterLogout = await context.cookies();
  expect(afterLogout.find((c) => c.name === "buvi_session")).toBeUndefined();
});

test("a developer-role user sees the developer/admin nav, not the client one", async ({ page }) => {
  await signInThroughKeycloak(page, "demo-developer");
  await expect(page).toHaveURL("http://localhost:3000/");

  await expect(page.getByRole("link", { name: "SQL Lab" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Data Sources" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Chat" })).toHaveCount(0);
  // A developer, not an org_admin, still should not see admin-only items.
  await expect(page.getByRole("link", { name: "Users" })).toHaveCount(0);
});

test("an unauthenticated visitor hitting a protected path is redirected to /login", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
});
