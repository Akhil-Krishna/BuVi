/**
 * Pure parsing -- no `server-only` guard needed here (unlike `lib/config.ts`
 * or `lib/auth/session.ts`): this file reads no environment variable and
 * calls no server-side API, so it is safe to unit-test directly and would be
 * harmless in a client bundle even though nothing actually imports it there.
 *
 * Parses one `Set-Cookie` response header value into just what a caller needs
 * to re-issue an equivalent cookie on this app's own origin: the value and,
 * if present, the max-age. Everything else (`Domain`, the original `Path`) is
 * deliberately dropped -- this app always re-issues a host-only cookie
 * scoped to its own origin (ADR 0018), never a copy of the upstream's
 * `Domain` attribute, which would be meaningless (or wrong) here.
 */
export function parseSetCookie(
  header: string,
  name: string
): { value: string; maxAgeSeconds?: number } | null {
  const parts = header.split(";").map((part) => part.trim());
  const [rawName, ...rest] = parts[0]?.split("=") ?? [];
  if (rawName !== name) return null;
  const value = rest.join("=");
  const maxAgeAttr = parts.find((part) => part.toLowerCase().startsWith("max-age="));
  const maxAgeSeconds = maxAgeAttr ? Number(maxAgeAttr.split("=")[1]) : undefined;
  return { value, maxAgeSeconds: Number.isFinite(maxAgeSeconds) ? maxAgeSeconds : undefined };
}

/** Every `Set-Cookie` header on a fetch `Response`, using the modern
 * multi-value API when the runtime provides it (Node's `undici`-backed
 * `fetch` does) so cookies are never merged into one comma-joined string. */
export function allSetCookies(response: Response): string[] {
  const headers = response.headers as Headers & { getSetCookie?: () => string[] };
  if (typeof headers.getSetCookie === "function") {
    return headers.getSetCookie();
  }
  const single = response.headers.get("set-cookie");
  return single ? [single] : [];
}

export function findCookie(
  response: Response,
  name: string
): { value: string; maxAgeSeconds?: number } | null {
  for (const header of allSetCookies(response)) {
    const parsed = parseSetCookie(header, name);
    if (parsed) return parsed;
  }
  return null;
}
