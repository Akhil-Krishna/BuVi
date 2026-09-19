"""Data access for the `dashboard` schema. Every query also runs under RLS for the session's
tenant (Section 19); the explicit `tenant_id` filters are the second layer, not the only one."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard_service.infrastructure.db.models import Artifact, Dashboard, ShareLink, Tile


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    # --- artifacts -----------------------------------------------------------------------
    async def add_artifact(self, artifact: Artifact) -> Artifact:
        self._session.add(artifact)
        await self._session.flush()
        await self._session.refresh(artifact)
        return artifact

    async def get_artifact(self, tenant_id: uuid.UUID, artifact_id: uuid.UUID) -> Artifact | None:
        result = await self._session.execute(
            select(Artifact).where(Artifact.tenant_id == tenant_id, Artifact.id == artifact_id)
        )
        return result.scalar_one_or_none()

    async def get_artifact_tenant_id(self, artifact_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(
            select(Artifact.tenant_id).where(Artifact.id == artifact_id)
        )
        return result.scalar_one_or_none()

    # --- dashboards ----------------------------------------------------------------------
    async def add_dashboard(self, dashboard: Dashboard) -> Dashboard:
        self._session.add(dashboard)
        await self._session.flush()
        await self._session.refresh(dashboard)
        return dashboard

    async def get_dashboard(
        self, tenant_id: uuid.UUID, dashboard_id: uuid.UUID, *, for_update: bool = False
    ) -> Dashboard | None:
        statement = select(Dashboard).where(
            Dashboard.tenant_id == tenant_id, Dashboard.id == dashboard_id
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_dashboard_tenant_id(self, dashboard_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(
            select(Dashboard.tenant_id).where(Dashboard.id == dashboard_id)
        )
        return result.scalar_one_or_none()

    async def list_visible_dashboards(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, *, limit: int, after: uuid.UUID | None
    ) -> tuple[list[Dashboard], uuid.UUID | None]:
        """The caller's own dashboards plus `tenant`-visibility ones; keyset on `id`."""
        statement = (
            select(Dashboard)
            .where(
                Dashboard.tenant_id == tenant_id,
                or_(Dashboard.owner_id == user_id, Dashboard.visibility == "tenant"),
            )
            .order_by(Dashboard.id)
            .limit(limit + 1)
        )
        if after is not None:
            statement = statement.where(Dashboard.id > after)
        rows = list((await self._session.execute(statement)).scalars().all())
        if len(rows) > limit:
            return rows[:limit], rows[limit - 1].id
        return rows, None

    async def touch_dashboard(self, dashboard: Dashboard) -> None:
        await self._session.execute(
            update(Dashboard).where(Dashboard.id == dashboard.id).values(updated_at=func.now())
        )

    # --- tiles ---------------------------------------------------------------------------
    async def list_tiles(self, tenant_id: uuid.UUID, dashboard_id: uuid.UUID) -> list[Tile]:
        result = await self._session.execute(
            select(Tile)
            .where(Tile.tenant_id == tenant_id, Tile.dashboard_id == dashboard_id)
            .order_by(Tile.created_at, Tile.id)
        )
        return list(result.scalars().all())

    async def add_tile(self, tile: Tile) -> Tile:
        self._session.add(tile)
        await self._session.flush()
        await self._session.refresh(tile)
        return tile

    async def get_tile(self, tenant_id: uuid.UUID, tile_id: uuid.UUID) -> Tile | None:
        result = await self._session.execute(
            select(Tile).where(Tile.tenant_id == tenant_id, Tile.id == tile_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_tile_tenant_id(self, tile_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(select(Tile.tenant_id).where(Tile.id == tile_id))
        return result.scalar_one_or_none()

    async def update_tile(
        self, tile: Tile, *, position: dict[str, Any] | None, overrides: dict[str, Any] | None
    ) -> Tile:
        if position is not None:
            tile.position = position
        if overrides is not None:
            tile.overrides = overrides
        await self._session.flush()
        await self._session.refresh(tile)
        return tile

    # --- share links (Phase A10) ---------------------------------------------------------
    async def add_share_link(self, link: ShareLink) -> ShareLink:
        self._session.add(link)
        await self._session.flush()
        await self._session.refresh(link)
        return link

    async def list_share_links(
        self, tenant_id: uuid.UUID, dashboard_id: uuid.UUID
    ) -> list[ShareLink]:
        result = await self._session.execute(
            select(ShareLink)
            .where(ShareLink.tenant_id == tenant_id, ShareLink.dashboard_id == dashboard_id)
            .order_by(ShareLink.created_at.desc(), ShareLink.id)
        )
        return list(result.scalars().all())

    async def count_active_share_links(
        self, tenant_id: uuid.UUID, dashboard_id: uuid.UUID, now: dt.datetime
    ) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(ShareLink)
            .where(
                ShareLink.tenant_id == tenant_id,
                ShareLink.dashboard_id == dashboard_id,
                ShareLink.revoked_at.is_(None),
                ShareLink.expires_at > now,
            )
        )
        return int(result.scalar_one())

    async def revoke_share_link(
        self, tenant_id: uuid.UUID, dashboard_id: uuid.UUID, link_id: uuid.UUID, now: dt.datetime
    ) -> ShareLink | None:
        link = (
            await self._session.execute(
                select(ShareLink).where(
                    ShareLink.tenant_id == tenant_id,
                    ShareLink.dashboard_id == dashboard_id,
                    ShareLink.id == link_id,
                )
            )
        ).scalar_one_or_none()
        if link is not None and link.revoked_at is None:
            link.revoked_at = now
            await self._session.flush()
        return link

    async def find_share_link(self, token_hash: str) -> ShareLink | None:
        """Only inside `share_lookup_scope`: the one lookup made before a tenant is known."""
        result = await self._session.execute(
            select(ShareLink).where(ShareLink.token_hash == token_hash)
        )
        return result.scalar_one_or_none()
