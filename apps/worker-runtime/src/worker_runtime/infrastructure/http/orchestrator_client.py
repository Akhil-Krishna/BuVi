"""analytics-orchestrator's internal routes, called with this service's scoped tokens: run
execution, and storing consumed usage events (Phase A11)."""

from __future__ import annotations

import uuid

import httpx

from platform_auth import SERVICE_AUTH_HEADER, ServiceTokenClient, ServiceTokenError
from platform_contracts import BillingUsageRecorded
from worker_runtime.application.services.run_dispatcher import OrchestratorUnavailableError
from worker_runtime.core.config import SCOPE_EXECUTE, SCOPE_USAGE_WRITE

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

    async def store_usage(self, records: list[BillingUsageRecorded]) -> None:
        body = {"records": [record.model_dump(mode="json") for record in records]}
        try:
            token = await self._tokens.token_for(AUDIENCE, frozenset({SCOPE_USAGE_WRITE}))
            response = await self._http.post(
                f"{self._base}/internal/v1/billing/usage-records",
                json=body,
                headers={SERVICE_AUTH_HEADER: f"Bearer {token}"},
                timeout=httpx.Timeout(30.0, connect=3.0),
            )
        except (ServiceTokenError, httpx.HTTPError, KeyError, ValueError):
            raise OrchestratorUnavailableError() from None
        if response.status_code != 200:
            # A refusal (scope, validation) is fixed by configuration, not by dropping usage.
            raise OrchestratorUnavailableError()
