"""Dashboard share links and the guest snapshot (Sections 2 `guest`, 7.3, 8.6; Phase A10).

* **The owner decides.** Only the dashboard's owner creates, lists and revokes its links, with
  `dashboard:share`; creating one needs a fresh step-up (checked by the route).
* **The token is a bearer credential.** 256 random bits, returned once, stored as SHA-256,
  time-boxed (`share_link_max_hours`), revocable at once. An unknown, expired or revoked token
  gets the same 404.
* **A guest sees the charts, not the platform.** The snapshot carries the dashboard's name and
  each tile's title, chart spec, overrides, position and data. It carries no ids, SQL, source
  references or users. A tile whose data is over the export threshold, or has expired, comes
  without data.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dashboard_service.application.services.dashboards import DashboardService
from dashboard_service.application.services.ports import ResultReader
from dashboard_service.core.config import Settings
from dashboard_service.domain.errors import NotFoundError, ShareLinkLimitError
from dashboard_service.infrastructure.audit.sink import AuditRecord, AuditSink
from dashboard_service.infrastructure.db.models import ShareLink
from dashboard_service.infrastructure.db.repositories.dashboard_repository import (
    DashboardRepository,
)
from dashboard_service.infrastructure.db.session import share_lookup_scope, tenant_scope
from dashboard_service.infrastructure.http.clients import (
    DependencyUnavailableError,
    ResultGoneError,
    ResultNotFoundError,
)
from platform_auth import Principal

logger = logging.getLogger(__name__)

#: `secrets.token_urlsafe(32)`: 43 URL-safe characters. Anything else is not a token.
_TOKEN_LENGTH = 43


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class IssuedLink:
    link: ShareLink
    token: str
    url: str


@dataclass(frozen=True)
class SnapshotTile:
    title: str
    position: dict[str, Any]
    chart_spec: dict[str, Any]
    overrides: dict[str, Any]
    data: dict[str, Any] | None
    data_status: str  # "ok" | "expired" | "too_large"


@dataclass(frozen=True)
class Snapshot:
    name: str
    expires_at: dt.datetime
    tiles: list[SnapshotTile]


class ShareLinkService:
    def __init__(
        self,
        *,
        repository: DashboardRepository,
        dashboards: DashboardService,
        audit: AuditSink,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._dashboards = dashboards
        self._audit = audit
        self._settings = settings

    async def create(
        self, principal: Principal, dashboard_id: uuid.UUID, *, hours: int | None
    ) -> IssuedLink:
        dashboard = await self._dashboards.owned(principal, dashboard_id)
        now = dt.datetime.now(dt.UTC)
        active = await self._repository.count_active_share_links(
            dashboard.tenant_id, dashboard.id, now
        )
        if active >= self._settings.max_active_share_links:
            raise ShareLinkLimitError()
        lifetime = min(
            hours or self._settings.share_link_default_hours, self._settings.share_link_max_hours
        )
        token = secrets.token_urlsafe(32)
        link = await self._repository.add_share_link(
            ShareLink(
                tenant_id=dashboard.tenant_id,
                dashboard_id=dashboard.id,
                token_hash=token_hash(token),
                created_by=uuid.UUID(principal.user_id),
                expires_at=now + dt.timedelta(hours=lifetime),
            )
        )
        await self._record(
            principal,
            "dashboard.share_link.created",
            link,
            after={"dashboard_id": str(dashboard.id), "expires_at": link.expires_at.isoformat()},
        )
        return IssuedLink(link, token, f"{self._settings.share_base_url}{token}")

    async def list_links(self, principal: Principal, dashboard_id: uuid.UUID) -> list[ShareLink]:
        dashboard = await self._dashboards.share_managed(principal, dashboard_id)
        return await self._repository.list_share_links(dashboard.tenant_id, dashboard.id)

    async def revoke(
        self, principal: Principal, dashboard_id: uuid.UUID, link_id: uuid.UUID
    ) -> None:
        dashboard = await self._dashboards.share_managed(principal, dashboard_id)
        now = dt.datetime.now(dt.UTC)
        link = await self._repository.revoke_share_link(
            dashboard.tenant_id, dashboard.id, link_id, now
        )
        if link is None:
            raise NotFoundError()
        await self._record(
            principal,
            "dashboard.share_link.revoked",
            link,
            before={"expires_at": link.expires_at.isoformat()},
            after={"revoked_at": (link.revoked_at or now).isoformat()},
        )

    async def _record(
        self,
        principal: Principal,
        event_type: str,
        link: ShareLink,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        await self._audit.record(
            AuditRecord(
                tenant_id=link.tenant_id,
                actor_user_id=uuid.UUID(principal.user_id),
                event_type=event_type,
                resource_type="share_link",
                resource_id=str(link.id),
                before_state=before,
                after_state=after,
            )
        )


class SnapshotService:
    """The public, token-gated read. No principal: the token is the only credential."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        results: ResultReader,
        settings: Settings,
    ) -> None:
        self._factory = session_factory
        self._results = results
        self._settings = settings

    async def snapshot(self, token: str) -> Snapshot:
        if len(token) != _TOKEN_LENGTH or not all(c.isalnum() or c in "-_" for c in token):
            raise NotFoundError()
        # Read-only sessions: closing them ends the transaction and keeps loaded values
        # (a rollback would expire them).
        async with share_lookup_scope(self._factory) as lookup:
            link = await DashboardRepository(lookup).find_share_link(token_hash(token))
        now = dt.datetime.now(dt.UTC)
        if link is None or link.revoked_at is not None or link.expires_at <= now:
            raise NotFoundError()  # unknown, revoked and expired look the same

        async with tenant_scope(self._factory, link.tenant_id) as db:
            repository = DashboardRepository(db)
            dashboard = await repository.get_dashboard(link.tenant_id, link.dashboard_id)
            if dashboard is None:
                raise NotFoundError()
            tiles = await repository.list_tiles(link.tenant_id, dashboard.id)
            artifacts = {
                t.artifact_id: await repository.get_artifact(link.tenant_id, t.artifact_id)
                for t in tiles
            }
        view: list[SnapshotTile] = []
        for tile in tiles:
            artifact = artifacts.get(tile.artifact_id)
            if artifact is None:
                continue
            data, status = await self._data(link.tenant_id, artifact.query_result_ref)
            view.append(
                SnapshotTile(
                    title=artifact.title,
                    position=tile.position,
                    chart_spec=artifact.chart_spec,
                    overrides=tile.overrides,
                    data=data,
                    data_status=status,
                )
            )
        return Snapshot(name=dashboard.name, expires_at=link.expires_at, tiles=view)

    async def _data(self, tenant_id: uuid.UUID, handle: str) -> tuple[dict[str, Any] | None, str]:
        try:
            rows = await self._results.read(tenant_id, handle)
        except (ResultGoneError, ResultNotFoundError):
            return None, "expired"
        except DependencyUnavailableError:
            logger.warning("guest snapshot could not read a tile's data")
            return None, "expired"
        if rows.row_count > self._settings.export_step_up_rows:
            return None, "too_large"  # a guest can never step up (Section 7.3)
        return {"columns": rows.columns, "rows": rows.rows}, "ok"
