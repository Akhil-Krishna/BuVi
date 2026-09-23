/**
 * The attributes this app sets on the cookies it issues to the browser
 * (ADR 0018). Extracted into one pure, testable place so that `secure` being
 * environment-conditional is *provable* rather than merely visible: a
 * `Secure` cookie set over plain HTTP is silently dropped by the browser, so
 * hardcoding it either way breaks one environment or the other.
 *
 * Pure -- no `server-only` guard, no Next.js import -- so the unit test can
 * flip `NODE_ENV` and assert both branches.
 */
export type IssuedCookieOptions = {
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  maxAge?: number;
};

/** True only in a production build. Next.js sets `NODE_ENV=production` for
 * `next build`/`next start`; dev and test runs get `development`/`test`. */
export function shouldSetSecureCookie(nodeEnv: string | undefined): boolean {
  return nodeEnv === "production";
}

export function issuedCookieOptions(maxAgeSeconds?: number): IssuedCookieOptions {
  return {
    httpOnly: true,
    secure: shouldSetSecureCookie(process.env.NODE_ENV),
    sameSite: "lax",
    path: "/",
    maxAge: maxAgeSeconds,
  };
}
