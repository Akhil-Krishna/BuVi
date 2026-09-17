"""`dashboard.tile.pinned` on JetStream stream `DASHBOARD` (Section 18.1)."""

from __future__ import annotations

import nats
from nats.js.api import StreamConfig
from nats.js.errors import BadRequestError

from dashboard_service.core.config import TILE_PINNED_SUBJECT
from platform_contracts import DashboardTilePinned


class JetStreamDashboardEvents:
    def __init__(self, client: nats.NATS) -> None:
        self._client = client
        self._js = client.jetstream()

    @classmethod
    async def connect(cls, url: str, *, stream: str) -> JetStreamDashboardEvents:
        client = await nats.connect(url, connect_timeout=3, max_reconnect_attempts=-1)
        js = client.jetstream()
        config = StreamConfig(name=stream, subjects=["dashboard.>"], duplicate_window=120.0)
        try:
            await js.add_stream(config)
        except BadRequestError:
            await js.update_stream(config)
        return cls(client)

    async def tile_pinned(self, event: DashboardTilePinned) -> None:
        await self._js.publish(
            TILE_PINNED_SUBJECT,
            event.model_dump_json().encode(),
            timeout=3.0,
            headers={"Nats-Msg-Id": str(event.tile_id)},
        )

    @property
    def connected(self) -> bool:
        return bool(self._client.is_connected)

    async def close(self) -> None:
        await self._client.drain()
