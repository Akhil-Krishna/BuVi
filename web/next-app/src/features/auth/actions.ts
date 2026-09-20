"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { CALLBACK_URL, GATEWAY_URL, TRANSACTION_COOKIE } from "@/lib/config";
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

  const response = await fetchRedirect(url);
  if (response.status !== 307 && response.status !== 303) {
    throw new Error(`identity-service /auth/login returned ${response.status}`);
  }
  if (!response.location) {
    throw new Error("identity-service /auth/login did not return a redirect target");
  }

  const transaction = response.setCookies
    .map((header) => parseSetCookie(header, TRANSACTION_COOKIE))
    .find((parsed) => parsed !== null);
  if (transaction) {
    const store = await cookies();
    store.set(TRANSACTION_COOKIE, transaction.value, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/",
      maxAge: transaction.maxAgeSeconds,
    });
  }

  redirect(response.location);
}
