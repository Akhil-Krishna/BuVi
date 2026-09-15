"""Data-source endpoints (Section 9; Sections 7.2, 7.3, 13.1).

Every endpoint composes the coarse permission with, where a path carries an id, the
resource-tenant check (Section 7.2: cross-tenant ids answer 404), and `/secret` adds
step-up (Section 7.3). The gateway performs the coarse checks too; this service
re-checks everything (Section 6.3).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from metadata_service.api.v1.schemas import (
    ConnectionSecretRequest,
    ConnectivityTestResponse,
    DataSourceCreateRequest,
    DataSourceListResponse,
    DataSourceResponse,
    SyncResponse,
)
from metadata_service.application.services.data_source_service import NewDataSource
from metadata_service.dependencies import (
    ScopedRepo,
    build_catalog_sync_service,
    build_data_source_service,
    client_ip,
    load_data_source_tenant_id,
)
from metadata_service.domain.errors import ValidationFailedError
from metadata_service.domain.value_objects.connection import ConnectionSecret
from platform_auth import Principal, require_permission, require_resource_owner, require_step_up
from platform_auth.permissions import PERM_DATA_MANAGE

router = APIRouter(tags=["data-sources"])

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

DataManage = Annotated[Principal, Depends(require_permission(PERM_DATA_MANAGE))]
OwnsDataSource = Annotated[Principal, Depends(require_resource_owner(load_data_source_tenant_id))]
StepUp = Annotated[Principal, Depends(require_step_up)]


@router.get("/data-sources", response_model=DataSourceListResponse)
async def list_data_sources(
    request: Request,
    principal: DataManage,
    repository: ScopedRepo,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[uuid.UUID | None, Query()] = None,
) -> DataSourceListResponse:
    """Tenant-scoped list (Section 9: `data:manage`)."""
    items, next_cursor = await build_data_source_service(request, repository).list(
        principal, limit=limit, cursor=cursor
    )
    return DataSourceListResponse(
        items=[DataSourceResponse.model_validate(item) for item in items],
        next_cursor=str(next_cursor) if next_cursor else None,
    )


@router.post(
    "/data-sources", response_model=DataSourceResponse, status_code=status.HTTP_201_CREATED
)
async def create_data_source(
    request: Request,
    payload: DataSourceCreateRequest,
    principal: DataManage,
    repository: ScopedRepo,
) -> DataSourceResponse:
    """Create a connection record; `pending` until credentials are set (Section 9)."""
    created = await build_data_source_service(request, repository).create(
        principal,
        NewDataSource(
            name=payload.name,
            engine=payload.engine,
            host_label=payload.host_label,
            database_name=payload.database_name,
            allowed_schemas=tuple(payload.allowed_schemas),
        ),
        ip_address=client_ip(request),
    )
    return DataSourceResponse.model_validate(created)


@router.get("/data-sources/{data_source_id}", response_model=DataSourceResponse)
async def read_data_source(
    request: Request,
    data_source_id: uuid.UUID,
    principal: DataManage,
    _owns: OwnsDataSource,
    repository: ScopedRepo,
) -> DataSourceResponse:
    """One data source, including `status` and `last_sync_at` (ADR 0004)."""
    found = await build_data_source_service(request, repository).get(principal, data_source_id)
    return DataSourceResponse.model_validate(found)


@router.post("/data-sources/{data_source_id}/secret", response_model=DataSourceResponse)
async def set_data_source_secret(
    request: Request,
    data_source_id: uuid.UUID,
    payload: ConnectionSecretRequest,
    principal: DataManage,
    _owns: OwnsDataSource,
    _step_up: StepUp,
    repository: ScopedRepo,
) -> DataSourceResponse:
    """Write credentials to Vault (Section 9: `data:manage`, step-up). Never echoed back."""
    try:
        secret = ConnectionSecret(
            host=payload.host,
            port=payload.port,
            username=payload.username,
            password=payload.password.get_secret_value(),
            sslmode=payload.sslmode,
        )
    except ValueError:
        raise ValidationFailedError() from None
    updated = await build_data_source_service(request, repository).set_secret(
        principal, data_source_id, secret, ip_address=client_ip(request)
    )
    return DataSourceResponse.model_validate(updated)


@router.post("/data-sources/{data_source_id}/test", response_model=ConnectivityTestResponse)
async def test_data_source(
    request: Request,
    data_source_id: uuid.UUID,
    principal: DataManage,
    _owns: OwnsDataSource,
    repository: ScopedRepo,
) -> ConnectivityTestResponse:
    """Sanitized connectivity result only (Section 9, 13.1)."""
    outcome = await build_data_source_service(request, repository).test(principal, data_source_id)
    return ConnectivityTestResponse(
        data_source_id=outcome.data_source.id,
        ok=outcome.result.ok,
        code=outcome.result.code.value,
        message=outcome.result.message,
        tables_discovered=outcome.result.tables_discovered,
        latency_ms=outcome.result.latency_ms,
        status=outcome.data_source.status,
    )


@router.post("/data-sources/{data_source_id}/sync", response_model=SyncResponse)
async def sync_data_source(
    request: Request,
    data_source_id: uuid.UUID,
    principal: DataManage,
    _owns: OwnsDataSource,
    repository: ScopedRepo,
) -> SyncResponse:
    """Introspect and populate the catalog (Phase A3: synchronous for the MVP)."""
    outcome = await build_catalog_sync_service(request, repository).sync(principal, data_source_id)
    return SyncResponse(
        data_source_id=outcome.data_source.id,
        ok=outcome.ok,
        code=outcome.code.value,
        message=outcome.message,
        status=outcome.data_source.status,
        tables_synced=outcome.tables_synced,
        columns_synced=outcome.columns_synced,
        relationships_synced=outcome.relationships_synced,
        snapshot_id=outcome.snapshot_id,
        synced_at=outcome.synced_at,
    )
