"""Catalog read endpoints (Section 9: `catalog:read` + resource-tenant check)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from metadata_service.api.v1.data_sources import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    CatalogRead,
    OwnsDataSource,
)
from metadata_service.api.v1.schemas import (
    ColumnRefResponse,
    ColumnResponse,
    RelationshipResponse,
    TableDetailResponse,
    TableListResponse,
    TableSummaryResponse,
)
from metadata_service.application.queries.catalog_queries import CatalogQueries
from metadata_service.dependencies import ScopedRepo
from metadata_service.infrastructure.db.models import CatalogTable
from metadata_service.infrastructure.db.repositories.metadata_repository import ColumnRef

router = APIRouter(tags=["catalog"])


def _summary(table: CatalogTable, column_count: int) -> TableSummaryResponse:
    return TableSummaryResponse(
        id=table.id,
        data_source_id=table.data_source_id,
        schema_name=table.schema_name,
        table_name=table.table_name,
        description=table.description,
        row_count_estimate=table.row_count_estimate,
        is_visible_to_agent=table.is_visible_to_agent,
        column_count=column_count,
    )


def _ref(ref: ColumnRef) -> ColumnRefResponse:
    return ColumnRefResponse(
        column_id=ref.column_id,
        table_id=ref.table_id,
        schema_name=ref.schema_name,
        table_name=ref.table_name,
        column_name=ref.column_name,
    )


@router.get("/data-sources/{data_source_id}/tables", response_model=TableListResponse)
async def list_tables(
    data_source_id: uuid.UUID,
    principal: CatalogRead,
    _owns: OwnsDataSource,
    repository: ScopedRepo,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
) -> TableListResponse:
    page = await CatalogQueries(repository=repository).list_tables(
        principal, data_source_id, limit=limit, cursor=cursor
    )
    return TableListResponse(
        items=[_summary(table, count) for table, count in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/data-sources/{data_source_id}/tables/{table_id}", response_model=TableDetailResponse)
async def read_table(
    data_source_id: uuid.UUID,
    table_id: uuid.UUID,
    principal: CatalogRead,
    _owns: OwnsDataSource,
    repository: ScopedRepo,
) -> TableDetailResponse:
    detail = await CatalogQueries(repository=repository).get_table(
        principal, data_source_id, table_id
    )
    summary = _summary(detail.table, detail.column_count)
    return TableDetailResponse(
        **summary.model_dump(),
        columns=[ColumnResponse.model_validate(column) for column in detail.columns],
        relationships=[
            RelationshipResponse(
                id=rel.id,
                relationship_type=rel.relationship_type,
                from_column=_ref(rel.from_column),
                to_column=_ref(rel.to_column),
            )
            for rel in detail.relationships
        ],
    )
