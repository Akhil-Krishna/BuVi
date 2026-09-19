"""Loads a data source's query policy from metadata-service (Section 13: "load connection policy
(metadata-service, cached)").

The tenant sent is the authenticated principal's; metadata-service answers 404 for a data source
of any other tenant. Cached briefly per (tenant, data source): a catalog sync or a status change
is picked up within `ttl_seconds`.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Protocol

import httpx

from platform_auth import SERVICE_AUTH_HEADER, ServiceTokenClient, ServiceTokenError
from platform_observability import REQUEST_ID_HEADER, request_id_var
from query_gateway.core.config import SCOPE_QUERY_POLICY
from query_gateway.domain.errors import (
    NotFoundError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from query_gateway.domain.value_objects.policy import ColumnPolicy, DataSourcePolicy, TablePolicy

METADATA_AUDIENCE = "metadata-service"


class PolicyLoader(Protocol):
    async def load(self, tenant_id: uuid.UUID, data_source_id: uuid.UUID) -> DataSourcePolicy: ...

    async def has_sql_grant(
        self, tenant_id: uuid.UUID, data_source_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool: ...


def parse_policy(body: dict[str, Any]) -> DataSourcePolicy:
    return DataSourcePolicy(
        data_source_id=uuid.UUID(body["data_source_id"]),
        tenant_id=uuid.UUID(body["tenant_id"]),
        engine=str(body["engine"]),
        database_name=str(body["database_name"]),
        allowed_schemas=tuple(str(s) for s in body["allowed_schemas"]),
        status=str(body["status"]),
        secret_ref=str(body["secret_ref"]),
        tables=tuple(
            TablePolicy(
                schema_name=str(t["schema_name"]),
                table_name=str(t["table_name"]),
                is_visible_to_agent=bool(t["is_visible_to_agent"]),
                columns=tuple(
                    ColumnPolicy(str(c["column_name"]), str(c["data_type"]), bool(c["is_pii"]))
                    for c in t["columns"]
                ),
            )
            for t in body["tables"]
        ),
    )


class MetadataPolicyClient:
    def __init__(
        self,
        *,
        base_url: str,
        http: httpx.AsyncClient,
        tokens: ServiceTokenClient,
        ttl_seconds: float,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._tokens = tokens
        self._ttl = ttl_seconds
        self._cache: dict[tuple[uuid.UUID, uuid.UUID], tuple[float, DataSourcePolicy]] = {}

    async def load(self, tenant_id: uuid.UUID, data_source_id: uuid.UUID) -> DataSourcePolicy:
        key = (tenant_id, data_source_id)
        cached = self._cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            token = await self._tokens.token_for(METADATA_AUDIENCE, frozenset({SCOPE_QUERY_POLICY}))
        except (ServiceTokenError, httpx.HTTPError, KeyError, ValueError) as exc:
            raise UpstreamUnavailableError() from exc
        headers = {SERVICE_AUTH_HEADER: f"Bearer {token}"}
        if request_id := request_id_var.get():
            headers[REQUEST_ID_HEADER] = request_id
        try:
            response = await self._http.get(
                f"{self._base}/internal/v1/data-sources/{data_source_id}/query-policy",
                params={"tenant_id": str(tenant_id)},
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError() from exc
        except httpx.TransportError as exc:
            raise UpstreamUnavailableError() from exc
        if response.status_code == 404:
            self._cache.pop(key, None)
            raise NotFoundError()
        if response.status_code != 200:
            raise UpstreamUnavailableError()
        try:
            policy = parse_policy(response.json())
        except (KeyError, TypeError, ValueError) as exc:
            raise UpstreamUnavailableError() from exc
        if policy.tenant_id != tenant_id or policy.data_source_id != data_source_id:
            raise NotFoundError()
        if self._ttl > 0:
            self._cache[key] = (time.monotonic() + self._ttl, policy)
        return policy

    async def has_sql_grant(
        self, tenant_id: uuid.UUID, data_source_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool:
        """Per-connection `sql:execute` grant (Section 7.1). Never cached: a revocation
        applies to the next query."""
        try:
            token = await self._tokens.token_for(METADATA_AUDIENCE, frozenset({SCOPE_QUERY_POLICY}))
        except (ServiceTokenError, httpx.HTTPError, KeyError, ValueError) as exc:
            raise UpstreamUnavailableError() from exc
        try:
            response = await self._http.get(
                f"{self._base}/internal/v1/data-sources/{data_source_id}/sql-grants/{user_id}",
                params={"tenant_id": str(tenant_id)},
                headers={SERVICE_AUTH_HEADER: f"Bearer {token}"},
            )
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError() from exc
        except httpx.TransportError as exc:
            raise UpstreamUnavailableError() from exc
        if response.status_code != 200:
            raise UpstreamUnavailableError()
        try:
            return response.json()["granted"] is True
        except (KeyError, TypeError, ValueError) as exc:
            raise UpstreamUnavailableError() from exc

    async def ready(self) -> bool:
        try:
            response = await self._http.get(f"{self._base}/health/live", timeout=2.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200
