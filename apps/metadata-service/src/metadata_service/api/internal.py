"""Internal service-to-service API (Section 6.3). Never routed by api-gateway.

* `GET /internal/v1/data-sources/{id}/query-policy?tenant_id=` -- the connection policy
  query-gateway needs before it validates or executes a query (Section 13: "load connection
  policy (metadata-service, cached)"). Requires `metadata-service:query-policy`.

The response carries `secret_ref` -- the Vault *pointer* query-gateway reads the credential
from -- and never a credential. The tenant is supplied by the caller from the principal it
authenticated; the lookup runs RLS-bound to that tenant, so a data source owned by any other
tenant is `404` in both layers.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from metadata_service.api.v1.schemas import (
    ActiveDataSource,
    ActiveDataSourceList,
    AgentContextColumn,
    AgentContextResponse,
    AgentContextTable,
    QueryPolicyResponse,
    QueryPolicyTable,
)
from metadata_service.core.config import SCOPE_CONTEXT, SCOPE_QUERY_POLICY
from metadata_service.dependencies import get_session_factory
from metadata_service.domain.errors import NotFoundError
from metadata_service.infrastructure.db.repositories.metadata_repository import (
    MetadataRepository,
)
from metadata_service.infrastructure.db.session import tenant_scope
from platform_auth import ServiceIdentity, require_service_scope

router = APIRouter(prefix="/internal/v1", tags=["internal"])


@router.get("/data-sources/{data_source_id}/query-policy", response_model=QueryPolicyResponse)
async def query_policy(
    request: Request,
    data_source_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Query()],
    _service: Annotated[ServiceIdentity, Depends(require_service_scope(SCOPE_QUERY_POLICY))],
) -> QueryPolicyResponse:
    async with tenant_scope(get_session_factory(request), tenant_id) as db:
        repository = MetadataRepository(db)
        data_source = await repository.get_data_source(tenant_id, data_source_id)
        if data_source is None:
            raise NotFoundError()
        catalog = await repository.list_catalog(tenant_id, data_source_id)
        await db.commit()
    return QueryPolicyResponse(
        data_source_id=data_source.id,
        tenant_id=data_source.tenant_id,
        engine=data_source.engine,
        database_name=data_source.database_name,
        allowed_schemas=list(data_source.allowed_schemas),
        status=data_source.status,
        secret_ref=data_source.secret_ref,
        last_sync_at=data_source.last_sync_at,
        tables=[QueryPolicyTable.from_catalog(table, columns) for table, columns in catalog],
    )


ContextScope = Annotated[ServiceIdentity, Depends(require_service_scope(SCOPE_CONTEXT))]


@router.get("/data-sources", response_model=ActiveDataSourceList)
async def active_data_sources(
    request: Request, tenant_id: Annotated[uuid.UUID, Query()], _service: ContextScope
) -> ActiveDataSourceList:
    async with tenant_scope(get_session_factory(request), tenant_id) as db:
        sources = await MetadataRepository(db).list_active_data_sources(tenant_id)
        await db.commit()
    return ActiveDataSourceList(
        items=[
            ActiveDataSource(
                id=s.id, name=s.name, engine=s.engine, status=s.status, last_sync_at=s.last_sync_at
            )
            for s in sources
        ]
    )


@router.get("/data-sources/{data_source_id}/context", response_model=AgentContextResponse)
async def agent_context(
    request: Request,
    data_source_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Query()],
    _service: ContextScope,
) -> AgentContextResponse:
    async with tenant_scope(get_session_factory(request), tenant_id) as db:
        repository = MetadataRepository(db)
        data_source = await repository.get_data_source(tenant_id, data_source_id)
        if data_source is None:
            raise NotFoundError()
        catalog = await repository.list_catalog(tenant_id, data_source_id)
        await db.commit()
    return AgentContextResponse(
        data_source_id=data_source.id,
        tenant_id=data_source.tenant_id,
        engine=data_source.engine,
        status=data_source.status,
        allowed_schemas=list(data_source.allowed_schemas),
        last_sync_at=data_source.last_sync_at,
        tables=[
            AgentContextTable(
                schema_name=table.schema_name,
                table_name=table.table_name,
                description=table.description,
                row_count_estimate=table.row_count_estimate,
                columns=[
                    AgentContextColumn(
                        column_name=c.column_name, data_type=c.data_type, description=c.description
                    )
                    for c in columns
                    if not c.is_pii
                ],
            )
            for table, columns in catalog
            if table.is_visible_to_agent
        ],
    )
