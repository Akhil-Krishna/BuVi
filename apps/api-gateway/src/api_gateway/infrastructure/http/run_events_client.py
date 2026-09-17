"""analytics-orchestrator's persisted run events, for the SSE bridge (Sections 11, 18).

The tenant sent is the authenticated principal's; the orchestrator answers 404 for a run of any
other tenant, so the stream never starts for it.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from api_gateway.domain.errors import NotFoundError, UpstreamTimeoutError, UpstreamUnavailableError
from platform_auth import SERVICE_AUTH_HEADER, ServiceTokenClient, ServiceTokenError
from platform_observability import REQUEST_ID_HEADER, request_id_var

AUDIENCE = "analytics-orchestrator"
SCOPE_EVENTS = "analytics-orchestrator:events"


class RunEventsClient:
    def __init__(
        self, *, base_url: str, http: httpx.AsyncClient, tokens: ServiceTokenClient
    ) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._tokens = tokens

    async def replay(
        self, tenant_id: str, run_id: uuid.UUID, after_seq: int
    ) -> tuple[str, list[dict[str, Any]]]:
        try:
            token = await self._tokens.token_for(AUDIENCE, frozenset({SCOPE_EVENTS}))
        except (ServiceTokenError, httpx.HTTPError, KeyError, ValueError) as exc:
            raise UpstreamUnavailableError() from exc
        headers = {SERVICE_AUTH_HEADER: f"Bearer {token}"}
        if request_id := request_id_var.get():
            headers[REQUEST_ID_HEADER] = request_id
        try:
            response = await self._http.get(
                f"{self._base}/internal/v1/runs/{run_id}/events",
                params={"tenant_id": tenant_id, "after_seq": after_seq},
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError() from exc
        except httpx.TransportError as exc:
            raise UpstreamUnavailableError() from exc
        if response.status_code == 404:
            raise NotFoundError()
        if response.status_code != 200:
            raise UpstreamUnavailableError()
        try:
            body = response.json()
            return str(body["status"]), list(body["events"])
        except (KeyError, TypeError, ValueError) as exc:
            raise UpstreamUnavailableError() from exc
