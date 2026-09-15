"""Catalog reads: tables of a data source, and one table with columns and relationships.

Section 9 lists no catalog read endpoint, but the Phase A3 DoD requires one ("see
tables/columns in the catalog API"); ADR 0004 records the two routes added.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass

from metadata_service.application.services.data_source_service import tenant_of
from metadata_service.domain.errors import InvalidCursorError, NotFoundError
from metadata_service.infrastructure.db.models import CatalogColumn, CatalogTable
from metadata_service.infrastructure.db.repositories.metadata_repository import (
    MetadataRepository,
    RelationshipRow,
)
from platform_auth import Principal

MAX_CURSOR_LENGTH = 1024


def encode_cursor(key: tuple[str, str]) -> str:
    raw = json.dumps(list(key), separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str]:
    if len(cursor) > MAX_CURSOR_LENGTH:
        raise InvalidCursorError()
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise InvalidCursorError() from None
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(part, str) for part in value)
    ):
        raise InvalidCursorError()
    return value[0], value[1]


@dataclass(frozen=True)
class TablePage:
    items: list[tuple[CatalogTable, int]]
    next_cursor: str | None


@dataclass(frozen=True)
class TableDetail:
    table: CatalogTable
    column_count: int
    columns: list[CatalogColumn]
    relationships: list[RelationshipRow]


class CatalogQueries:
    def __init__(self, *, repository: MetadataRepository) -> None:
        self._repository = repository

    async def _require_data_source(self, tenant_id: uuid.UUID, data_source_id: uuid.UUID) -> None:
        if await self._repository.get_data_source(tenant_id, data_source_id) is None:
            raise NotFoundError()

    async def list_tables(
        self,
        principal: Principal,
        data_source_id: uuid.UUID,
        *,
        limit: int,
        cursor: str | None,
    ) -> TablePage:
        tenant_id = tenant_of(principal)
        after = decode_cursor(cursor) if cursor else None
        await self._require_data_source(tenant_id, data_source_id)
        rows, next_key = await self._repository.list_tables(
            tenant_id, data_source_id, limit=limit, after=after
        )
        return TablePage(items=rows, next_cursor=encode_cursor(next_key) if next_key else None)

    async def get_table(
        self, principal: Principal, data_source_id: uuid.UUID, table_id: uuid.UUID
    ) -> TableDetail:
        tenant_id = tenant_of(principal)
        found = await self._repository.get_table(tenant_id, data_source_id, table_id)
        if found is None:
            raise NotFoundError()
        table, column_count = found
        return TableDetail(
            table=table,
            column_count=column_count,
            columns=await self._repository.list_columns(table.id),
            relationships=await self._repository.list_relationships_for_table(tenant_id, table.id),
        )
