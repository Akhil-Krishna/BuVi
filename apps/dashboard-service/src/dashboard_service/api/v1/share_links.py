"""Share links and the public, token-gated snapshot (Section 9).

Creating a link: the owner, with `dashboard:share` and a fresh step-up. Listing and revoking:
the owner or any `org_admin`, needing only `dashboard:read`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response, status

from dashboard_service.api.v1.schemas import (
    ShareLinkCreatedResponse,
    ShareLinkCreateRequest,
    ShareLinkListResponse,
    ShareLinkResponse,
    SnapshotResponse,
    SnapshotTileResponse,
)
from dashboard_service.dependencies import (
    ScopedRepo,
    build_share_link_service,
    build_snapshot_service,
    load_dashboard_tenant_id,
)
from dashboard_service.infrastructure.db.models import ShareLink
from platform_auth import Principal, require_permission, require_resource_owner, require_step_up
from platform_auth.permissions import PERM_DASHBOARD_READ, PERM_DASHBOARD_SHARE

router = APIRouter(tags=["share-links"])
public_router = APIRouter(tags=["guest-share"])

DashboardShare = Annotated[Principal, Depends(require_permission(PERM_DASHBOARD_SHARE))]
#: Listing and revoking: the owner or an org_admin (checked in the service), whatever their
#: sharing permission is today.
DashboardRead = Annotated[Principal, Depends(require_permission(PERM_DASHBOARD_READ))]
OwnsDashboard = Annotated[Principal, Depends(require_resource_owner(load_dashboard_tenant_id))]
StepUp = Annotated[Principal, Depends(require_step_up)]
Token = Annotated[str, Path(min_length=1, max_length=128)]

#: The token is in the URL: keep it out of caches, referrers and search indexes.
_GUEST_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}


def _link(link: ShareLink) -> ShareLinkResponse:
    return ShareLinkResponse(
        id=link.id,
        created_by=link.created_by,
        created_at=link.created_at,
        expires_at=link.expires_at,
        revoked_at=link.revoked_at,
        active=link.revoked_at is None and link.expires_at > dt.datetime.now(dt.UTC),
    )


@router.post(
    "/dashboards/{dashboard_id}/share-links",
    response_model=ShareLinkCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_share_link(
    request: Request,
    dashboard_id: uuid.UUID,
    principal: DashboardShare,
    _owns: OwnsDashboard,
    _step_up: StepUp,
    repository: ScopedRepo,
    payload: ShareLinkCreateRequest | None = None,
) -> ShareLinkCreatedResponse:
    """Owner only, fresh step-up. The token is in this response and nowhere else."""
    issued = await build_share_link_service(request, repository).create(
        principal, dashboard_id, hours=payload.expires_in_hours if payload else None
    )
    return ShareLinkCreatedResponse(
        **_link(issued.link).model_dump(), token=issued.token, url=issued.url
    )


@router.get("/dashboards/{dashboard_id}/share-links", response_model=ShareLinkListResponse)
async def list_share_links(
    request: Request,
    dashboard_id: uuid.UUID,
    principal: DashboardRead,
    _owns: OwnsDashboard,
    repository: ScopedRepo,
) -> ShareLinkListResponse:
    links = await build_share_link_service(request, repository).list_links(principal, dashboard_id)
    return ShareLinkListResponse(items=[_link(link) for link in links])


@router.delete(
    "/dashboards/{dashboard_id}/share-links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def revoke_share_link(
    request: Request,
    dashboard_id: uuid.UUID,
    link_id: uuid.UUID,
    principal: DashboardRead,
    _owns: OwnsDashboard,
    repository: ScopedRepo,
) -> Response:
    """Effective at once: the next guest request with that token is a 404."""
    await build_share_link_service(request, repository).revoke(principal, dashboard_id, link_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@public_router.get("/share/{token}", response_model=SnapshotResponse)
async def guest_snapshot(request: Request, token: Token, response: Response) -> SnapshotResponse:
    """Public and token-gated (Section 9). An unknown, expired or revoked token is a 404."""
    snapshot = await build_snapshot_service(request).snapshot(token)
    response.headers.update(_GUEST_HEADERS)
    return SnapshotResponse(
        name=snapshot.name,
        expires_at=snapshot.expires_at,
        tiles=[
            SnapshotTileResponse(
                title=t.title,
                position=t.position,
                chart_spec=t.chart_spec,
                overrides=t.overrides,
                data=t.data,
                data_status=t.data_status,  # type: ignore[arg-type]
            )
            for t in snapshot.tiles
        ],
    )
