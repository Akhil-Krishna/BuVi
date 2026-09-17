"""Dashboard and tile endpoints (Section 9): `dashboard:read` / `dashboard:pin` plus the
resource-tenant check; changes are the dashboard owner's only."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from dashboard_service.api.v1.schemas import (
    DashboardCreateRequest,
    DashboardDetailResponse,
    DashboardListResponse,
    DashboardResponse,
    PinRequest,
    TilePosition,
    TileResponse,
    TileUpdateRequest,
)
from dashboard_service.dependencies import (
    ScopedRepo,
    build_dashboard_service,
    load_dashboard_tenant_id,
    load_tile_tenant_id,
)
from dashboard_service.domain.errors import InvalidCursorError
from dashboard_service.infrastructure.db.models import Dashboard, Tile
from platform_auth import Principal, require_permission, require_resource_owner
from platform_auth.permissions import PERM_DASHBOARD_PIN, PERM_DASHBOARD_READ

router = APIRouter(tags=["dashboards"])

DashboardRead = Annotated[Principal, Depends(require_permission(PERM_DASHBOARD_READ))]
DashboardPin = Annotated[Principal, Depends(require_permission(PERM_DASHBOARD_PIN))]
OwnsDashboard = Annotated[Principal, Depends(require_resource_owner(load_dashboard_tenant_id))]
OwnsTile = Annotated[Principal, Depends(require_resource_owner(load_tile_tenant_id))]


def _dashboard(dashboard: Dashboard, principal: Principal) -> DashboardResponse:
    return DashboardResponse(
        id=dashboard.id,
        name=dashboard.name,
        visibility=dashboard.visibility,
        owner_id=dashboard.owner_id,
        is_owner=str(dashboard.owner_id) == principal.user_id,
        created_at=dashboard.created_at,
        updated_at=dashboard.updated_at,
    )


def _tile(tile: Tile) -> TileResponse:
    return TileResponse(
        id=tile.id,
        dashboard_id=tile.dashboard_id,
        artifact_id=tile.artifact_id,
        chart_spec_version=tile.chart_spec_version,
        position=TilePosition.model_validate(tile.position),
        overrides=tile.overrides,
        created_at=tile.created_at,
    )


@router.get("/dashboards", response_model=DashboardListResponse)
async def list_dashboards(
    request: Request,
    principal: DashboardRead,
    repository: ScopedRepo,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
) -> DashboardListResponse:
    try:
        after = uuid.UUID(cursor) if cursor else None
    except ValueError:
        raise InvalidCursorError() from None
    rows, next_key = await build_dashboard_service(request, repository).list_visible(
        principal, limit=limit, cursor=after
    )
    return DashboardListResponse(
        items=[_dashboard(d, principal) for d in rows],
        next_cursor=str(next_key) if next_key else None,
    )


@router.post("/dashboards", response_model=DashboardResponse, status_code=status.HTTP_201_CREATED)
async def create_dashboard(
    request: Request,
    payload: DashboardCreateRequest,
    principal: DashboardPin,
    repository: ScopedRepo,
) -> DashboardResponse:
    dashboard = await build_dashboard_service(request, repository).create(
        principal, name=payload.name, visibility=payload.visibility
    )
    return _dashboard(dashboard, principal)


@router.get("/dashboards/{dashboard_id}", response_model=DashboardDetailResponse)
async def get_dashboard(
    request: Request,
    dashboard_id: uuid.UUID,
    principal: DashboardRead,
    _owns: OwnsDashboard,
    repository: ScopedRepo,
) -> DashboardDetailResponse:
    view = await build_dashboard_service(request, repository).get(principal, dashboard_id)
    return DashboardDetailResponse(
        **_dashboard(view.dashboard, principal).model_dump(),
        tiles=[_tile(t) for t in view.tiles],
    )


@router.post(
    "/dashboards/{dashboard_id}/tiles",
    response_model=TileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def pin_artifact(
    request: Request,
    dashboard_id: uuid.UUID,
    payload: PinRequest,
    principal: DashboardPin,
    _owns: OwnsDashboard,
    repository: ScopedRepo,
) -> TileResponse:
    """Section 32 Step D: pin an artifact; returns the created `DashboardTile`."""
    tile = await build_dashboard_service(request, repository).pin(
        principal, dashboard_id, payload.artifact_id
    )
    return _tile(tile)


@router.patch("/tiles/{tile_id}", response_model=TileResponse)
async def update_tile(
    request: Request,
    tile_id: uuid.UUID,
    payload: TileUpdateRequest,
    principal: DashboardPin,
    _owns: OwnsTile,
    repository: ScopedRepo,
) -> TileResponse:
    tile = await build_dashboard_service(request, repository).update_tile(
        principal,
        tile_id,
        position=payload.position.model_dump() if payload.position else None,
        overrides=payload.overrides,
    )
    return _tile(tile)
