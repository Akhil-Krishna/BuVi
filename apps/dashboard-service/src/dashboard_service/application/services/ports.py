"""What the application layer needs from outside, as protocols."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from dashboard_service.infrastructure.http.clients import ChartCheck, ResultRows
from platform_contracts import DashboardTilePinned


class ChartValidator(Protocol):
    async def check(
        self,
        chart_spec: Any,
        result_schema: list[dict[str, Any]],
        overrides: dict[str, Any] | None = None,
    ) -> ChartCheck: ...


class ResultReader(Protocol):
    async def read(self, tenant_id: uuid.UUID, handle: str) -> ResultRows: ...


class DashboardEvents(Protocol):
    async def tile_pinned(self, event: DashboardTilePinned) -> None: ...
