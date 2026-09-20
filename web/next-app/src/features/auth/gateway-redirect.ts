import "server-only";
import * as http from "node:http";
import * as https from "node:https";

/**
 * A GET request that reads a 3xx response's status, `Location`, and every
 * `Set-Cookie` header without following the redirect.
 *
 * This can't be done with the platform `fetch()` in Next.js's Node runtime:
 * undici (the `fetch` implementation Node and Next.js both use) treats
 * `redirect: "manual"` as the WHATWG *browser* opaque-redirect mode --
 * status 0, no headers readable -- rather than returning the raw 3xx (undici
 * issue #1193). Node's core `http`/`https` client has no such limitation
 * (it never auto-follows redirects at all), so it is used here instead, for
 * this one relay call only (ADR 0018). Everywhere else in this app that talks
 * to api-gateway, the response is not a redirect and plain `fetch()` is fine.
 */
export function fetchRedirect(
  url: URL
): Promise<{ status: number; location: string | null; setCookies: string[] }> {
  const client = url.protocol === "https:" ? https : http;
  return new Promise((resolve, reject) => {
    const req = client.get(url, (res) => {
      const setCookieHeader = res.headers["set-cookie"];
      resolve({
        status: res.statusCode ?? 0,
        location: (res.headers.location as string | undefined) ?? null,
        setCookies: setCookieHeader ?? [],
      });
      res.resume(); // discard the body; nothing here has one worth reading
    });
    req.on("error", reject);
  });
}
