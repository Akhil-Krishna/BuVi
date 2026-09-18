"""Outbound HTTP to MCP servers under Section 15's request-time controls.

* **Resolved and checked now.** The endpoint's host is resolved when the call is made, and every
  address must pass `EgressPolicy` -- an address approved yesterday proves nothing today (DNS
  rebinding).
* **Pinned.** The request goes to the checked address itself, with the hostname only in the
  `Host` header and as TLS SNI. The certificate is still verified against the hostname, so
  pinning never weakens TLS.
* **No connection reuse.** Two tenants' servers can share an address (a CDN, a shared host). A
  pooled TLS connection verified for one hostname must never carry another's request, so every
  request opens its own connection.
* **Never redirected**, bounded in time and size, and only `application/json` or
  `text/event-stream` bodies are read.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from mcp_gateway.domain.policies.endpoint import Endpoint
from platform_egress import EgressPolicy, HostResolutionError, IPAddress, resolve_host

Resolver = Callable[[str, int], Awaitable[list[IPAddress]]]


class DestinationNotAllowedError(Exception):
    """The endpoint now resolves to an address Section 15 refuses."""


class UpstreamFailure(Exception):  # noqa: N818 - carries a fixed reason code, not the server's text
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class UpstreamLimits:
    timeout_seconds: float
    connect_timeout_seconds: float
    max_response_bytes: int


def build_http_client(
    limits: UpstreamLimits, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """The client for MCP traffic only: no redirects, no keep-alive, no retries, no env proxy."""
    return httpx.AsyncClient(
        transport=transport
        or httpx.AsyncHTTPTransport(
            limits=httpx.Limits(max_keepalive_connections=0), retries=0, trust_env=False
        ),
        timeout=httpx.Timeout(limits.timeout_seconds, connect=limits.connect_timeout_seconds),
        follow_redirects=False,
        trust_env=False,
    )


@dataclass(frozen=True)
class PinnedEndpoint:
    endpoint: Endpoint
    address: IPAddress

    @property
    def request_url(self) -> str:
        host = f"[{self.address}]" if self.address.version == 6 else str(self.address)
        return f"{self.endpoint.scheme}://{host}:{self.endpoint.port}{self.endpoint.path}"

    @property
    def extensions(self) -> dict[str, str]:
        """TLS SNI (and so certificate verification) against the hostname, not the address."""
        if self.endpoint.scheme == "https" and ":" not in self.endpoint.host:
            return {"sni_hostname": self.endpoint.host}
        return {}


async def pin(endpoint: Endpoint, egress: EgressPolicy, resolver: Resolver) -> PinnedEndpoint:
    try:
        addresses = await resolver(endpoint.host, endpoint.port)
    except HostResolutionError:
        raise UpstreamFailure("unresolvable") from None
    if not egress.permits(endpoint.host, addresses):
        raise DestinationNotAllowedError()
    return PinnedEndpoint(endpoint, addresses[0])


class EgressClient:
    """Sends requests to one pinned endpoint and reads bounded responses."""

    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        egress: EgressPolicy,
        limits: UpstreamLimits,
        resolver: Resolver = resolve_host,
    ) -> None:
        self._http = http
        self._egress = egress
        self.limits = limits
        self._resolver = resolver

    async def pin(self, endpoint: Endpoint) -> PinnedEndpoint:
        return await pin(endpoint, self._egress, self._resolver)

    @asynccontextmanager
    async def post(
        self, pinned: PinnedEndpoint, body: bytes, headers: dict[str, str]
    ) -> AsyncIterator[httpx.Response]:
        request = self._http.build_request(
            "POST",
            pinned.request_url,
            content=body,
            headers={**headers, "Host": pinned.endpoint.host_header},
            extensions=pinned.extensions,
        )
        try:
            response = await self._http.send(request, stream=True)
        except httpx.TimeoutException:
            raise UpstreamFailure("timeout") from None
        except httpx.HTTPError:
            raise UpstreamFailure("unreachable") from None
        try:
            if response.is_redirect or 300 <= response.status_code < 400:
                raise UpstreamFailure("redirect_refused")
            yield response
        finally:
            await response.aclose()

    async def delete(self, pinned: PinnedEndpoint, headers: dict[str, str]) -> None:
        """Best effort (ending a session); failures are ignored."""
        try:
            async with asyncio.timeout(self.limits.connect_timeout_seconds):
                await self._http.request(
                    "DELETE",
                    pinned.request_url,
                    headers={**headers, "Host": pinned.endpoint.host_header},
                    extensions=pinned.extensions,
                )
        except (httpx.HTTPError, TimeoutError):
            return

    async def read_body(self, response: httpx.Response) -> bytes:
        body = bytearray()
        try:
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > self.limits.max_response_bytes:
                    raise UpstreamFailure("response_too_large")
        except httpx.HTTPError:
            raise UpstreamFailure("unreachable") from None
        return bytes(body)

    async def read_lines(self, response: httpx.Response) -> AsyncIterator[str]:
        """Lines of a text stream, capped on raw bytes: a server that never sends a newline
        still hits the cap (httpx's own line iterator would buffer the whole line first)."""
        size = 0
        pending = b""
        try:
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self.limits.max_response_bytes:
                    raise UpstreamFailure("response_too_large")
                pending += chunk
                *lines, pending = pending.split(b"\n")
                for line in lines:
                    yield line.rstrip(b"\r").decode("utf-8", errors="replace")
        except httpx.HTTPError:
            raise UpstreamFailure("unreachable") from None
        if pending:
            yield pending.rstrip(b"\r").decode("utf-8", errors="replace")


def content_type(response: httpx.Response) -> str:
    value: str = response.headers.get("content-type", "")
    return value.split(";", 1)[0].strip().lower()
