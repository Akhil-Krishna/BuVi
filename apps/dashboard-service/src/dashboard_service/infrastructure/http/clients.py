"""visualization-service (ChartSpec validation) and query-gateway (stored result rows). Each call
carries this service's scoped token and the request id; failures map to typed port errors."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from dashboard_service.core.config import SCOPE_RESULTS, SCOPE_VALIDATE
from platform_auth import SERVICE_AUTH_HEADER, ServiceTokenClient, ServiceTokenError
from platform_observability import REQUEST_ID_HEADER, request_id_var


class DependencyUnavailableError(Exception):
    """A backing service could not answer."""


class ResultGoneError(Exception):
    """query-gateway says the stored result has expired."""


class ResultNotFoundError(Exception):
    """query-gateway does not recognise the handle for this tenant."""


@dataclass(frozen=True)
class ChartCheck:
    valid: bool
    chart_spec: dict[str, Any] | None
    problems: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResultRows:
    columns: list[dict[str, str]]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    expires_at: str


class _ServiceClient:
    audience: str

    def __init__(
        self, *, base_url: str, http: httpx.AsyncClient, tokens: ServiceTokenClient
    ) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._tokens = tokens

    async def _post(self, path: str, scope: str, body: dict[str, Any]) -> httpx.Response:
        try:
            token = await self._tokens.token_for(self.audience, frozenset({scope}))
        except (ServiceTokenError, httpx.HTTPError, KeyError, ValueError):
            raise DependencyUnavailableError() from None
        headers = {SERVICE_AUTH_HEADER: f"Bearer {token}"}
        if request_id := request_id_var.get():
            headers[REQUEST_ID_HEADER] = request_id
        try:
            return await self._http.post(f"{self._base}{path}", json=body, headers=headers)
        except httpx.HTTPError:
            raise DependencyUnavailableError() from None


class VisualizationClient(_ServiceClient):
    audience = "visualization-service"

    async def check(
        self,
        chart_spec: Any,
        result_schema: list[dict[str, Any]],
        overrides: dict[str, Any] | None = None,
    ) -> ChartCheck:
        body: dict[str, Any] = {"chart_spec": chart_spec, "result_schema": result_schema}
        if overrides is not None:
            body["overrides"] = overrides
        response = await self._post("/internal/v1/chart-specs/validate", SCOPE_VALIDATE, body)
        if response.status_code != 200:
            raise DependencyUnavailableError()
        try:
            data = response.json()
            return ChartCheck(
                valid=bool(data["valid"]),
                chart_spec=data.get("chart_spec"),
                problems=[str(p) for p in data.get("problems", [])][:20],
            )
        except (KeyError, TypeError, ValueError):
            raise DependencyUnavailableError() from None


class QueryResultsClient(_ServiceClient):
    audience = "query-gateway"

    async def read(self, tenant_id: uuid.UUID, handle: str) -> ResultRows:
        response = await self._post(
            "/internal/v1/results/read",
            SCOPE_RESULTS,
            {"tenant_id": str(tenant_id), "result_handle": handle},
        )
        if response.status_code == 410:
            raise ResultGoneError()
        if response.status_code == 404:
            raise ResultNotFoundError()
        if response.status_code != 200:
            raise DependencyUnavailableError()
        try:
            data = response.json()
            return ResultRows(
                columns=[{"name": str(c["name"]), "type": str(c["type"])} for c in data["columns"]],
                rows=list(data["rows"]),
                row_count=int(data["row_count"]),
                truncated=bool(data["truncated"]),
                expires_at=str(data["expires_at"]),
            )
        except (KeyError, TypeError, ValueError):
            raise DependencyUnavailableError() from None
