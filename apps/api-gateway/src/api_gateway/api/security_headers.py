"""Default security response headers for the platform edge.

api-gateway is the only service a browser (or the Next.js BFF on its behalf) reaches, so the
response headers that harden that boundary belong here rather than in each owning service.

What this sets, and why only these:

* `X-Content-Type-Options: nosniff` -- never let a client re-interpret a JSON body as something
  executable.
* `X-Frame-Options: DENY` -- nothing this API returns is meant to be framed.
* `Referrer-Policy: no-referrer` -- URLs here carry ids, and one public route carries a share
  token (Section 9); those must not leak through `Referer`.
* `Cache-Control: no-store` -- every response is tenant-scoped or an auth step, so no shared
  cache or browser may retain one.

Deliberately **not** set here:

* **HSTS** belongs to whatever terminates TLS (Section 28). This process does not know whether
  the connection reached it over TLS, and a wrong `max-age` from an app that is sometimes run
  over plain HTTP in dev is worse than none.
* **Content-Security-Policy** governs how a *document* may load resources; this service returns
  JSON and SSE. The frontend sets its own CSP for the pages it serves (Track B).

Route- and upstream-set headers always win: the guest share snapshot proxied from
dashboard-service already sends its own `Cache-Control`/`Referrer-Policy`/`X-Robots-Tag`, and the
SSE run stream sends `Cache-Control: no-cache`. This only fills in what is absent.

Implemented as raw ASGI rather than `BaseHTTPMiddleware` so it never buffers a streaming
response -- the run-event stream (`text/event-stream`) must reach the client frame by frame.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_SECURITY_HEADERS: Final[Mapping[str, str]] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class SecurityHeadersMiddleware:
    """Adds `headers` to every HTTP response that does not already set them."""

    def __init__(self, app: ASGIApp, headers: Mapping[str, str] = DEFAULT_SECURITY_HEADERS) -> None:
        self._app = app
        self._headers = [
            (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_defaults(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                missing = [(k, v) for k, v in self._headers if k not in present]
                if missing:
                    message["headers"] = [*message.get("headers", []), *missing]
            await send(message)

        await self._app(scope, receive, send_with_defaults)
