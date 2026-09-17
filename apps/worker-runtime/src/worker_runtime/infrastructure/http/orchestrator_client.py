"""analytics-orchestrator's internal execute route, called with this service's scoped token."""

from __future__ import annotations

import uuid

import httpx

from platform_auth import SERVICE_AUTH_HEADER, ServiceTokenClient, ServiceTokenError
from worker_runtime.application.services.run_dispatcher import OrchestratorUnavailableError
from worker_runtime.core.config import SCOPE_EXECUTE

AUDIENCE = "analytics-orchestrator"


class OrchestratorClient:
    def __init__(
        self,
        *,
        base_url: str,
        http: httpx.AsyncClient,
        tokens: ServiceTokenClient,
        timeout_seconds: float,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._tokens = tokens
        self._timeout = timeout_seconds

    async def execute(self, tenant_id: uuid.UUID, run_id: uuid.UUID) -> int:
        try:
            token = await self._tokens.token_for(AUDIENCE, frozenset({SCOPE_EXECUTE}))
            response = await self._http.post(
                f"{self._base}/internal/v1/runs/{run_id}/execute",
                params={"tenant_id": str(tenant_id)},
                headers={SERVICE_AUTH_HEADER: f"Bearer {token}"},
                timeout=httpx.Timeout(self._timeout, connect=3.0),
            )
        except (ServiceTokenError, httpx.HTTPError, KeyError, ValueError):
            raise OrchestratorUnavailableError() from None
        if response.status_code >= 500:
            raise OrchestratorUnavailableError()
        return response.status_code
