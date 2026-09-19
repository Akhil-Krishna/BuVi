"""Webhook delivery under Section 15's request-time controls (Phase A11).

* The URL is re-checked (`parse_endpoint`) and its host resolved **now**; every address must pass
  `EgressPolicy`, and the request goes to the checked address itself with the hostname only in
  `Host` and TLS SNI (`platform_egress.pin_endpoint`). A subscription approved yesterday proves
  nothing about today's DNS answer.
* Never redirected, no keep-alive, no environment proxy, bounded in time. The response body is
  never read: only the status code matters, and a receiver's text is never stored or logged.
* A bounded number of attempts with back-off. A refused destination is not retried.
"""

from __future__ import annotations

import asyncio

import httpx

from notification_service.application.services.ports import DeliveryResult
from platform_egress import (
    DestinationNotAllowedError,
    EgressPolicy,
    EndpointRejected,
    HostResolutionError,
    Resolver,
    parse_endpoint,
    pin_endpoint,
    resolve_host,
)

_FINAL = frozenset({"destination_not_allowed", "url_invalid", "redirect_refused"})


def build_webhook_client(
    *,
    timeout_seconds: float,
    connect_timeout_seconds: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """The client for webhook traffic only: no redirects, no keep-alive, no retries, no proxy."""
    return httpx.AsyncClient(
        transport=transport
        or httpx.AsyncHTTPTransport(
            limits=httpx.Limits(max_keepalive_connections=0), retries=0, trust_env=False
        ),
        timeout=httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds),
        follow_redirects=False,
        trust_env=False,
    )


class PinnedWebhookSender:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        egress: EgressPolicy,
        attempts: int,
        backoff_seconds: float,
        resolver: Resolver = resolve_host,
    ) -> None:
        self._http = http
        self._egress = egress
        self._attempts = attempts
        self._backoff = backoff_seconds
        self._resolver = resolver

    async def deliver(self, url: str, body: bytes, headers: dict[str, str]) -> DeliveryResult:
        detail = "unreachable"
        for attempt in range(1, self._attempts + 1):
            detail = await self._attempt(url, body, headers)
            if detail == "delivered":
                return DeliveryResult(True, detail, attempt)
            if detail in _FINAL or attempt == self._attempts:
                return DeliveryResult(False, detail, attempt)
            await asyncio.sleep(self._backoff * attempt)
        return DeliveryResult(False, detail, self._attempts)

    async def _attempt(self, url: str, body: bytes, headers: dict[str, str]) -> str:
        try:
            endpoint = parse_endpoint(url, self._egress)
            pinned = await pin_endpoint(endpoint, self._egress, self._resolver)
        except EndpointRejected as rejected:
            return (
                "destination_not_allowed"
                if rejected.reason == "destination_not_allowed"
                else "url_invalid"
            )
        except DestinationNotAllowedError:
            return "destination_not_allowed"
        except HostResolutionError:
            return "unresolvable"
        request = self._http.build_request(
            "POST",
            pinned.request_url,
            content=body,
            headers={**headers, "Host": endpoint.host_header},
            extensions=pinned.extensions,
        )
        try:
            response = await self._http.send(request, stream=True)
        except httpx.TimeoutException:
            return "timeout"
        except httpx.HTTPError:
            return "unreachable"
        await response.aclose()
        if 300 <= response.status_code < 400:
            return "redirect_refused"
        if 200 <= response.status_code < 300:
            return "delivered"
        return f"status_{response.status_code}"
