"""Dashboards and tiles (Sections 9, 16): create, list, read, pin an artifact, change a tile."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from dashboard_service.application.services.ports import ChartValidator, DashboardEvents
from dashboard_service.domain.errors import (
    ChartSpecInvalidError,
    DashboardNotOwnerError,
    NotFoundError,
    UpstreamUnavailableError,
    ValidationFailedError,
)
from dashboard_service.domain.policies.dashboard_policy import (
    dashboard_access,
    next_position,
    position_problems,
)
from dashboard_service.infrastructure.db.models import Dashboard, Tile
from dashboard_service.infrastructure.db.repositories.dashboard_repository import (
    DashboardRepository,
)
from dashboard_service.infrastructure.http.clients import DependencyUnavailableError
from platform_auth import Principal
from platform_auth.permissions import ROLE_ORG_ADMIN
from platform_contracts import DashboardTilePinned
from platform_observability import request_id_var

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DashboardView:
    dashboard: Dashboard
    is_owner: bool
    tiles: list[Tile]


class DashboardService:
    def __init__(
        self,
        *,
        repository: DashboardRepository,
        visualization: ChartValidator,
        events: DashboardEvents,
    ) -> None:
        self._repository = repository
        self._visualization = visualization
        self._events = events

    @staticmethod
    def _ids(principal: Principal) -> tuple[uuid.UUID, uuid.UUID]:
        return uuid.UUID(principal.tenant_id), uuid.UUID(principal.user_id)

    async def _dashboard(
        self, principal: Principal, dashboard_id: uuid.UUID, *, write: bool
    ) -> Dashboard:
        tenant_id, _ = self._ids(principal)
        dashboard = await self._repository.get_dashboard(tenant_id, dashboard_id, for_update=write)
        if dashboard is None:
            raise NotFoundError()
        access = dashboard_access(
            owner_id=str(dashboard.owner_id),
            visibility=dashboard.visibility,
            user_id=principal.user_id,
        )
        if access == "hidden":
            raise NotFoundError()
        if write and access != "owner":
            raise DashboardNotOwnerError()
        return dashboard

    async def share_managed(self, principal: Principal, dashboard_id: uuid.UUID) -> Dashboard:
        """For listing and revoking share links: the owner, or any `org_admin` of the tenant
        (incident response on a leaked link). Stopping a share never needs more than starting
        one did."""
        if ROLE_ORG_ADMIN in principal.roles:
            tenant_id, _ = self._ids(principal)
            dashboard = await self._repository.get_dashboard(tenant_id, dashboard_id)
            if dashboard is None:
                raise NotFoundError()
            return dashboard
        return await self.owned(principal, dashboard_id)

    async def owned(self, principal: Principal, dashboard_id: uuid.UUID) -> Dashboard:
        """The dashboard, locked, if the caller owns it (404 if hidden, 403 if only visible)."""
        return await self._dashboard(principal, dashboard_id, write=True)

    async def create(self, principal: Principal, *, name: str, visibility: str) -> Dashboard:
        tenant_id, user_id = self._ids(principal)
        return await self._repository.add_dashboard(
            Dashboard(tenant_id=tenant_id, name=name, owner_id=user_id, visibility=visibility)
        )

    async def list_visible(
        self, principal: Principal, *, limit: int, cursor: uuid.UUID | None
    ) -> tuple[list[Dashboard], uuid.UUID | None]:
        tenant_id, user_id = self._ids(principal)
        return await self._repository.list_visible_dashboards(
            tenant_id, user_id, limit=limit, after=cursor
        )

    async def get(self, principal: Principal, dashboard_id: uuid.UUID) -> DashboardView:
        dashboard = await self._dashboard(principal, dashboard_id, write=False)
        tiles = await self._repository.list_tiles(dashboard.tenant_id, dashboard.id)
        return DashboardView(
            dashboard=dashboard, is_owner=str(dashboard.owner_id) == principal.user_id, tiles=tiles
        )

    async def pin(
        self, principal: Principal, dashboard_id: uuid.UUID, artifact_id: uuid.UUID
    ) -> Tile:
        tenant_id, user_id = self._ids(principal)
        dashboard = await self._dashboard(principal, dashboard_id, write=True)
        artifact = await self._repository.get_artifact(tenant_id, artifact_id)
        if artifact is None:
            raise NotFoundError()
        existing = await self._repository.list_tiles(tenant_id, dashboard.id)
        tile = await self._repository.add_tile(
            Tile(
                tenant_id=tenant_id,
                dashboard_id=dashboard.id,
                artifact_id=artifact.id,
                chart_spec_version=artifact.version,
                position=next_position(t.position for t in existing),
                overrides={},
            )
        )
        await self._repository.touch_dashboard(dashboard)
        await self._repository.commit()
        try:
            await self._events.tile_pinned(
                DashboardTilePinned(
                    tenant_id=tenant_id,
                    dashboard_id=dashboard.id,
                    tile_id=tile.id,
                    artifact_id=artifact.id,
                    user_id=user_id,
                    request_id=request_id_var.get(),
                )
            )
        except Exception as error:
            # The pin is committed; the notification (notification-service) is best effort.
            logger.warning(
                "tile pinned event not published",
                extra={"context": {"tile_id": str(tile.id), "error_type": type(error).__name__}},
            )
        return tile

    async def update_tile(
        self,
        principal: Principal,
        tile_id: uuid.UUID,
        *,
        position: dict[str, int] | None,
        overrides: dict[str, Any] | None,
    ) -> Tile:
        tenant_id, _ = self._ids(principal)
        tile = await self._repository.get_tile(tenant_id, tile_id)
        if tile is None:
            raise NotFoundError()
        dashboard = await self._dashboard(principal, tile.dashboard_id, write=True)
        if position is not None and position_problems(position):
            raise ValidationFailedError()
        if overrides is not None:
            artifact = await self._repository.get_artifact(tenant_id, tile.artifact_id)
            if artifact is None:
                raise NotFoundError()
            try:
                check = await self._visualization.check(
                    artifact.chart_spec, artifact.result_schema, overrides
                )
            except DependencyUnavailableError:
                raise UpstreamUnavailableError() from None
            if not check.valid:
                raise ChartSpecInvalidError(problems=check.problems)
        updated = await self._repository.update_tile(tile, position=position, overrides=overrides)
        await self._repository.touch_dashboard(dashboard)
        return updated
