"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { CALLBACK_URL, GATEWAY_URL, TRANSACTION_COOKIE } from "@/lib/config";
import { issuedCookieOptions } from "@/lib/cookie-options";
import { fetchRedirect } from "./gateway-redirect";
import { parseSetCookie } from "./cookie-relay";

/**
 * Section 6.1 step 2 (ADR 0018): relay identity-service's `GET /auth/login`
 * server-side, so the browser is redirected straight to Keycloak without ever
 * navigating to api-gateway itself. `redirect_uri` names this app's own
 * callback route -- identity-service checks it against a two-value allow-list
 * and rejects anything else with `400 INVALID_REDIRECT_URI`.
 */
export async function beginLogin(): Promise<never> {
  const url = new URL("/api/v1/auth/login", GATEWAY_URL);
  url.searchParams.set("redirect_uri", CALLBACK_URL);

  // A connection failure (api-gateway down, wrong `BUVI_GATEWAY_URL`, DNS) rejects rather than
  // returning a status, and would otherwise surface as an opaque "no message was provided"
  // Server Components error -- breaking this function's own contract of never rendering an
  // unhandled error page. `redirect()` throws to work, so it stays outside the try.
  let response: Awaited<ReturnType<typeof fetchRedirect>>;
  try {
    response = await fetchRedirect(url);
  } catch (error) {
    console.warn("[auth/login] api-gateway is not reachable", {
      gateway: url.origin,
      cause: error instanceof Error ? error.message : String(error),
    });
    redirect("/login?error=service_unavailable");
  }

  if (response.status !== 307 && response.status !== 303) {
    // Never an unhandled error page: the visitor is sent back to the sign-in
    // screen with a reason they can act on. 429 is the auth tier's per-IP
    // limit (Section 16) and means "wait", not "something is broken".
    console.warn("[auth/login] could not start sign-in", { status: response.status });
    redirect(response.status === 429 ? "/login?error=rate_limited" : "/login?error=sign_in_failed");
  }
  if (!response.location) {
    console.warn("[auth/login] no redirect target returned");
    redirect("/login?error=sign_in_failed");
  }

  const transaction = response.setCookies
    .map((header) => parseSetCookie(header, TRANSACTION_COOKIE))
    .find((parsed) => parsed !== null);
  if (transaction) {
    const store = await cookies();
    store.set(
      TRANSACTION_COOKIE,
      transaction.value,
      issuedCookieOptions(transaction.maxAgeSeconds)
    );
  }

  redirect(response.location);
}
