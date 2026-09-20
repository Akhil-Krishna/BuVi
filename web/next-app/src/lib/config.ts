/**
 * Server-only configuration (ADR 0018, spec Section 6.1).
 *
 * `GATEWAY_URL` is never sent to the browser -- it must not appear in any
 * `NEXT_PUBLIC_*` variable or client component. The browser only ever talks to
 * this app's own origin; every call to `GATEWAY_URL` happens in a Route
 * Handler or Server Action, server-side.
 */

import "server-only";

/** api-gateway's base URL. The only thing in this app that ever calls it. */
export const GATEWAY_URL = process.env.BUVI_GATEWAY_URL ?? "http://localhost:8000";

/**
 * This app's own OIDC callback route -- passed to identity-service's
 * `GET /auth/login` as `redirect_uri` (ADR 0018's two-value allow-list).
 * Must exactly match `IDENTITY_OIDC_FRONTEND_REDIRECT_URI` on identity-service
 * and a redirect URI registered with the IdP.
 */
export const CALLBACK_URL = process.env.BUVI_CALLBACK_URL ?? "http://localhost:3000/callback";

/** Cookie names, kept identical to identity-service's so a relayed Set-Cookie
 * header always names the cookie this app also expects to read back. */
export const SESSION_COOKIE = "buvi_session";
export const TRANSACTION_COOKIE = "buvi_oidc_txn";

/** Section 6.1 step 6/9: matches identity-service's session lifetime, used
 * only as the re-issued cookie's `maxAge` -- the session's real expiry is
 * always enforced server-side by identity-service, never by this value alone. */
export const SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60;
