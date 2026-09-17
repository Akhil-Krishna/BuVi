"""Dashboard wire contracts (Section 18.1)."""

from __future__ import annotations

import uuid

from pydantic import Field

from platform_contracts.analytics import _Versioned


class DashboardTilePinned(_Versioned):
    """`dashboard.tile.pinned`: dashboard-service -> notification-service (Phase A11)."""

    tenant_id: uuid.UUID
    dashboard_id: uuid.UUID
    tile_id: uuid.UUID
    artifact_id: uuid.UUID
    user_id: uuid.UUID
    request_id: str | None = Field(default=None, max_length=128)
