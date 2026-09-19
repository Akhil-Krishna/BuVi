"""`metadata.sync.completed` on JetStream stream `METADATA` (Section 18.1; Phase A11)."""

from __future__ import annotations

import nats
from nats.js.api import StreamConfig
from nats.js.errors import BadRequestError

from metadata_service.core.config import SYNC_COMPLETED_SUBJECT
from platform_contracts import MetadataSyncCompleted


class JetStreamMetadataEvents:
    def __init__(self, client: nats.NATS) -> None:
        self._client = client
        self._js = client.jetstream()

    @classmethod
    async def connect(cls, url: str, *, stream: str) -> JetStreamMetadataEvents:
        client = await nats.connect(url, connect_timeout=3, max_reconnect_attempts=-1)
        js = client.jetstream()
        config = StreamConfig(name=stream, subjects=["metadata.>"], duplicate_window=120.0)
        try:
            await js.add_stream(config)
        except BadRequestError:
            await js.update_stream(config)
        return cls(client)

    async def sync_completed(self, event: MetadataSyncCompleted) -> None:
        await self._js.publish(
            SYNC_COMPLETED_SUBJECT, event.model_dump_json().encode(), timeout=3.0
        )

    @property
    def connected(self) -> bool:
        return bool(self._client.is_connected)

    async def close(self) -> None:
        await self._client.drain()


class UnavailableMetadataEvents:
    connected = False

    async def sync_completed(self, event: MetadataSyncCompleted) -> None:  # noqa: ARG002
        raise ConnectionError("event stream unavailable")
