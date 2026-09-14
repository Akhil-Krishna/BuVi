"""Reverse proxy to owning services, over service-JWT auth (Section 6.3).

What crosses the hop, and what never does:

* forwarded: method, path, query, body, the user's own credential (cookie or
  `Authorization`), content negotiation, `Idempotency-Key`;
* replaced by the gateway: `X-Request-ID`, `X-Forwarded-*` (a client cannot choose
  the IP written to audit rows), and `X-Service-Authorization` (a client-supplied
  service token is stripped, never passed through);
* never forwarded either way: hop-by-hop headers.

Responses pass through unchanged -- status, body and every `Set-Cookie` -- so the
login redirect and session cookie reach the browser exactly as issued.
"""

from __future__ import annotations

from typing import Final

import httpx
from fastapi import Request, Response

from api_gateway.domain.errors import UpstreamTimeoutError, UpstreamUnavailableError
from platform_auth import SERVICE_AUTH_HEADER, ServiceTokenClient, ServiceTokenError
from platform_observability import REQUEST_ID_HEADER

HOP_BY_HOP: Final[frozenset[str]] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)
#: Inbound headers the gateway owns; a client's values are discarded.
GATEWAY_OWNED: Final[frozenset[str]] = frozenset(
    {
        SERVICE_AUTH_HEADER.lower(),
        REQUEST_ID_HEADER.lower(),
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
        "forwarded",
        "accept-encoding",
    }
)
_RESPONSE_DROP: Final[frozenset[str]] = HOP_BY_HOP | {"content-encoding", REQUEST_ID_HEADER.lower()}


class UpstreamProxy:
    def __init__(
        self, *, backends: dict[str, str], http: httpx.AsyncClient, tokens: ServiceTokenClient
    ) -> None:
        self._backends = {name: url.rstrip("/") for name, url in backends.items()}
        self._http = http
        self._tokens = tokens

    async def forward(
        self,
        request: Request,
        *,
        backend: str,
        scope: str,
        client_ip: str,
        request_id: str,
        body: bytes,
    ) -> Response:
        base = self._backends.get(backend)
        if base is None:
            raise UpstreamUnavailableError()
        try:
            service_token = await self._tokens.token_for(backend, frozenset({scope}))
        except (ServiceTokenError, httpx.HTTPError) as exc:
            raise UpstreamUnavailableError() from exc

        headers = [
            (key, value)
            for key, value in request.headers.items()
            if key.lower() not in HOP_BY_HOP and key.lower() not in GATEWAY_OWNED
        ]
        headers += [
            (SERVICE_AUTH_HEADER, f"Bearer {service_token}"),
            (REQUEST_ID_HEADER, request_id),
            ("X-Forwarded-For", client_ip),
            ("X-Forwarded-Proto", request.url.scheme),
            ("Accept-Encoding", "identity"),
        ]
        if host := request.headers.get("host"):
            headers.append(("X-Forwarded-Host", host))

        url = f"{base}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        try:
            upstream = await self._http.request(request.method, url, headers=headers, content=body)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError() from exc
        except httpx.TransportError as exc:
            raise UpstreamUnavailableError() from exc

        response = Response(content=upstream.content, status_code=upstream.status_code)
        for key, value in upstream.headers.multi_items():
            if key.lower() not in _RESPONSE_DROP:
                response.raw_headers.append(
                    (key.lower().encode("latin-1"), value.encode("latin-1"))
                )
        return response
