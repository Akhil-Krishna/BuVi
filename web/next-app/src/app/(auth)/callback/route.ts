import { NextResponse, type NextRequest } from "next/server";
import {
  GATEWAY_URL,
  SESSION_COOKIE,
  SESSION_MAX_AGE_SECONDS,
  TRANSACTION_COOKIE,
} from "@/lib/config";
import { findCookie } from "@/features/auth/cookie-relay";
import { issuedCookieOptions } from "@/lib/cookie-options";

/**
 * Section 6.1 step 4-6 (ADR 0018). This route -- not any api-gateway path --
 * is the OIDC redirect URI Keycloak actually sends the real browser to for the
 * Next.js-driven login flow (`Settings.oidc_frontend_redirect_uri` on
 * identity-service must equal this route's absolute URL).
 *
 * identity-service's own `GET /auth/callback` returns `204 No Content` --
 * correct for a server-to-server caller, useless for a real browser's
 * top-level navigation to land on. This route is that caller: it relays the
 * `code`/`state` and the transaction cookie server-side, then turns the 204
 * into a real redirect to the app, carrying the session cookie forward.
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const code = request.nextUrl.searchParams.get("code");
  const state = request.nextUrl.searchParams.get("state");
  const transactionCookie = request.cookies.get(TRANSACTION_COOKIE)?.value;

  if (!code || !state || !transactionCookie) {
    console.warn("[auth/callback] incomplete callback", {
      hasCode: Boolean(code),
      hasState: Boolean(state),
      hasTransactionCookie: Boolean(transactionCookie),
    });
    return NextResponse.redirect(new URL("/login?error=sign_in_failed", request.url));
  }

  const url = new URL("/api/v1/auth/callback", GATEWAY_URL);
  url.searchParams.set("code", code);
  url.searchParams.set("state", state);

  const upstream = await fetch(url, {
    headers: { cookie: `${TRANSACTION_COOKIE}=${transactionCookie}` },
  });

  if (upstream.status !== 204) {
    // A stale/replayed/tampered transaction, an expired code, or a genuine IdP
    // failure. The visitor only ever sees "sign-in failed" -- identity-service's
    // classification can name the realm, the client, or which check refused, so
    // it belongs in this server's log (Section 21), never in the redirect.
    const envelope = (await upstream.json().catch(() => null)) as {
      error?: { code?: string; request_id?: string };
    } | null;
    console.warn("[auth/callback] sign-in refused upstream", {
      status: upstream.status,
      code: envelope?.error?.code,
      requestId: envelope?.error?.request_id,
    });
    // Being rate limited is a "try again shortly", not a broken sign-in; the
    // visitor gets told which of the two it is.
    const reason = upstream.status === 429 ? "rate_limited" : "sign_in_failed";
    return NextResponse.redirect(new URL(`/login?error=${reason}`, request.url));
  }

  const session = findCookie(upstream, SESSION_COOKIE);
  const response = NextResponse.redirect(new URL("/", request.url));
  response.cookies.delete(TRANSACTION_COOKIE);

  if (session) {
    response.cookies.set(
      SESSION_COOKIE,
      session.value,
      issuedCookieOptions(session.maxAgeSeconds ?? SESSION_MAX_AGE_SECONDS)
    );
  }
  return response;
}
