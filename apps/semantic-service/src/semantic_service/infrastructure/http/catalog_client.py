"""metadata-service catalog lookup: resolves the table and column ids a definition references."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx

from platform_auth import SERVICE_AUTH_HEADER, ServiceTokenClient, ServiceTokenError
from platform_observability import REQUEST_ID_HEADER, request_id_var
from semantic_service.core.config import SCOPE_CATALOG_LOOKUP
from semantic_service.domain.policies.metric_expression import CatalogColumnRef, CatalogTableRef


class CatalogUnavailableError(Exception):
    pass


@dataclass(frozen=True)
class CatalogColumnLookup:
    ref: CatalogColumnRef
    table_id: uuid.UUID


@dataclass(frozen=True)
class CatalogLookup:
    tables: dict[uuid.UUID, CatalogTableRef]
    table_visibility: dict[uuid.UUID, bool]
    columns: dict[uuid.UUID, CatalogColumnLookup]


class MetadataCatalogClient:
    def __init__(
        self, *, base_url: str, http: httpx.AsyncClient, tokens: ServiceTokenClient
    ) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._tokens = tokens

    async def lookup(
        self,
        tenant_id: uuid.UUID,
        *,
        table_ids: list[uuid.UUID] | None = None,
        column_ids: list[uuid.UUID] | None = None,
    ) -> CatalogLookup:
        try:
            token = await self._tokens.token_for(
                "metadata-service", frozenset({SCOPE_CATALOG_LOOKUP})
            )
        except (ServiceTokenError, httpx.HTTPError, KeyError, ValueError):
            raise CatalogUnavailableError() from None
        headers = {SERVICE_AUTH_HEADER: f"Bearer {token}"}
        if request_id := request_id_var.get():
            headers[REQUEST_ID_HEADER] = request_id
        body = {
            "tenant_id": str(tenant_id),
            "table_ids": [str(i) for i in table_ids or []],
            "column_ids": [str(i) for i in column_ids or []],
        }
        try:
            response = await self._http.post(
                f"{self._base}/internal/v1/catalog/lookup", json=body, headers=headers
            )
        except httpx.HTTPError:
            raise CatalogUnavailableError() from None
        if response.status_code != 200:
            raise CatalogUnavailableError()
        try:
            data = response.json()
            tables = {
                uuid.UUID(t["id"]): CatalogTableRef(
                    table_name=str(t["table_name"]),
                    is_visible_to_agent=bool(t["is_visible_to_agent"]),
                    columns=tuple(
                        CatalogColumnRef(
                            name=str(c["column_name"]),
                            data_type=str(c["data_type"]),
                            is_pii=bool(c["is_pii"]),
                        )
                        for c in t["columns"]
                    ),
                )
                for t in data["tables"]
            }
            columns = {
                uuid.UUID(c["id"]): CatalogColumnLookup(
                    ref=CatalogColumnRef(
                        name=str(c["column_name"]),
                        data_type=str(c["data_type"]),
                        is_pii=bool(c["is_pii"]),
                    ),
                    table_id=uuid.UUID(c["table_id"]),
                )
                for c in data["columns"]
            }
        except (KeyError, TypeError, ValueError):
            raise CatalogUnavailableError() from None
        return CatalogLookup(
            tables=tables,
            table_visibility={i: t.is_visible_to_agent for i, t in tables.items()},
            columns=columns,
        )
