import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, SESSION_COOKIE } from "@/lib/config";

/**
 * The BFF proxy to api-gateway (spec Section 4.2). Every business API call the
 * app makes goes through here. It forwards the session cookie as a `Cookie`
 * header -- api-gateway accepts it directly and introspects it server-to-
 * server against identity-service; there is no access-token attachment or
 * token exchange to perform here (ADR 0018 -- Section 6.1 step 7's original
 * text describes a mechanism Track A did not build).
 *
 * Streams the upstream response body through unmodified -- `GET
 * /runs/{id}/events` (Section 11) is `text/event-stream` and must reach the
 * client frame by frame, not buffered.
 */

const FORWARDED_REQUEST_HEADERS = ["content-type", "accept", "idempotency-key"];
// Hop-by-hop or connection-specific headers that must never be replayed onto a
// different connection (RFC 7230 6.1), plus ones we set ourselves below.
const DROPPED_RESPONSE_HEADERS = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "content-encoding",
  "content-length",
  "set-cookie", // this proxy never sets a cookie on behalf of the API it fronts
]);

async function proxy(request: NextRequest, path: string[]): Promise<NextResponse> {
  const target = new URL(`/api/v1/${path.join("/")}`, GATEWAY_URL);
  target.search = request.nextUrl.search;

  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const session = request.cookies.get(SESSION_COOKIE)?.value;
  if (session) headers.set("cookie", `${SESSION_COOKIE}=${session}`);

  const hasBody = !["GET", "HEAD"].includes(request.method);
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      // Node's fetch requires this when streaming a request body.
      ...(hasBody ? { duplex: "half" } : {}),
      redirect: "manual",
      cache: "no-store",
    } as RequestInit & { duplex?: "half" });
  } catch {
    return NextResponse.json(
      { error: { code: "GATEWAY_UNAVAILABLE", message: "The service is unavailable." } },
      { status: 503 }
    );
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!DROPPED_RESPONSE_HEADERS.has(key.toLowerCase())) {
      responseHeaders.set(key, value);
    }
  });

  return new NextResponse(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

async function handler(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const { path } = await params;
  return proxy(request, path);
}

export { handler as GET, handler as POST, handler as PUT, handler as PATCH, handler as DELETE };
