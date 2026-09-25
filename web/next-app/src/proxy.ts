import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/config";

/**
 * Early request boundary (spec Section 4.2, 6.1 step 1). Next.js 16 renamed
 * `middleware.ts` to `proxy.ts` -- same runtime, same file-system convention
 * (lives beside `app/` under `src/`, since this project uses a `src` layout),
 * new name and exported function.
 *
 * This is deliberately a presence-only check: it redirects a request with no
 * session cookie at all to `/login`, so an unauthenticated visitor never even
 * renders a protected page's shell. It does NOT decode the cookie or call
 * `/auth/session` -- that would add a network round trip to every navigation,
 * and Section 4.2 is explicit that this file "MUST NOT be the sole
 * authorization layer." Role-specific checks (a `client` hitting `/admin/*`)
 * belong to each route group's own layout, which already has to fetch the
 * session to render the shell in the first place.
 */
const PUBLIC_PATHS = ["/login", "/callback", "/invitations", "/share"];

export function proxy(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl;
  if (PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`))) {
    return NextResponse.next();
  }

  const hasSession = request.cookies.has(SESSION_COOKIE);
  if (!hasSession) {
    const loginUrl = new URL("/login", request.url);
    return NextResponse.redirect(loginUrl);
  }
  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * Every path except:
     * - Next.js internals (_next/static, _next/image)
     * - the favicon and other top-level public files
     */
    "/((?!_next/static|_next/image|favicon.ico|.*\\.[\\w]+$).*)",
  ],
};
