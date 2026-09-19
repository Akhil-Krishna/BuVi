"""`identity.role.changed` on JetStream stream `IDENTITY` (Section 18.1; Phase A11)."""

from __future__ import annotations

import nats
from nats.js.api import StreamConfig
from nats.js.errors import BadRequestError

from identity_service.core.config import ROLE_CHANGED_SUBJECT
from platform_contracts import IdentityRoleChanged


class JetStreamIdentityEvents:
    def __init__(self, client: nats.NATS) -> None:
        self._client = client
        self._js = client.jetstream()

    @classmethod
    async def connect(cls, url: str, *, stream: str) -> JetStreamIdentityEvents:
        client = await nats.connect(url, connect_timeout=3, max_reconnect_attempts=-1)
        js = client.jetstream()
        config = StreamConfig(name=stream, subjects=["identity.>"], duplicate_window=120.0)
        try:
            await js.add_stream(config)
        except BadRequestError:
            await js.update_stream(config)
        return cls(client)

    async def role_changed(self, event: IdentityRoleChanged) -> None:
        await self._js.publish(ROLE_CHANGED_SUBJECT, event.model_dump_json().encode(), timeout=3.0)

    @property
    def connected(self) -> bool:
        return bool(self._client.is_connected)

    async def close(self) -> None:
        await self._client.drain()


class UnavailableIdentityEvents:
    """Used when NATS is off or unreachable: identity keeps working, events are dropped
    (logged by the caller)."""

    connected = False

    async def role_changed(self, event: IdentityRoleChanged) -> None:  # noqa: ARG002
        raise ConnectionError("event stream unavailable")

    async def close(self) -> None:
        return None
