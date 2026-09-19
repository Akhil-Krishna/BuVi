"""The JetStream wiring against a real NATS (Section 18.1): a published `dashboard.tile.pinned`
becomes a notification, and a durable consumer does not replay history on first start."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import nats
import pytest
from nats.js.api import StreamConfig

from notification_service.tests.conftest import PostgresInfo, running
from platform_auth import ServiceTokenIssuer
from platform_contracts import DashboardTilePinned

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def nats_url() -> Iterator[str]:
    from testcontainers.core.container import DockerContainer

    with DockerContainer("nats:2-alpine").with_command("-js").with_exposed_ports(4222) as container:
        url = f"nats://{container.get_container_host_ip()}:{container.get_exposed_port(4222)}"

        async def wait() -> None:
            deadline = time.monotonic() + 30
            while True:
                try:
                    client = await nats.connect(url, connect_timeout=1, max_reconnect_attempts=0)
                    await client.close()
                    return
                except Exception:
                    if time.monotonic() > deadline:
                        raise
                    await asyncio.sleep(0.3)

        asyncio.run(wait())
        yield url


async def _wait_for(predicate: Callable[[], Any], seconds: float = 20.0) -> None:
    deadline = time.monotonic() + seconds
    while not await predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.2)


def _pin(tenant: uuid.UUID, user: uuid.UUID) -> DashboardTilePinned:
    return DashboardTilePinned(
        tenant_id=tenant,
        dashboard_id=uuid.uuid4(),
        tile_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        user_id=user,
    )


async def test_published_pin_becomes_a_notification_and_history_is_not_replayed(
    nats_url: str,
    postgres: PostgresInfo,
    issuer: ServiceTokenIssuer,
    platform_db: Any,
    tenant: uuid.UUID,
) -> None:
    client = await nats.connect(nats_url)
    js = client.jetstream()
    # The producer's stream, created first, with a message from before the service existed.
    await js.add_stream(
        StreamConfig(name="DASHBOARD", subjects=["dashboard.>"], duplicate_window=120.0)
    )
    old_user = uuid.uuid4()
    await js.publish("dashboard.tile.pinned", _pin(tenant, old_user).model_dump_json().encode())

    async with running(
        postgres, issuer, consumers_enabled=True, nats_url=nats_url, fetch_timeout_seconds=0.5
    ) as h:
        user = h.identity.add_user(tenant, {"client"})
        consumers = h.app.state.consumers
        await _wait_for(lambda: _true(all(c.connected for c in consumers)))
        await js.publish(
            "dashboard.tile.pinned", _pin(tenant, user.user_id).model_dump_json().encode()
        )

        async def delivered() -> bool:
            count = await platform_db.fetchval(
                "SELECT count(*) FROM notification.notifications WHERE tenant_id = $1", tenant
            )
            return bool(count == 1)

        await _wait_for(delivered)
        ready = await h.client.get("/health/ready")
        assert ready.json()["checks"] == {"database": "ok", "events": "ok"}
    await client.close()
    key = await platform_db.fetchval(
        "SELECT event_key FROM notification.notifications WHERE tenant_id = $1", tenant
    )
    assert key == "DASHBOARD:2"  # stream sequence 1 (history) was never delivered


async def _true(value: bool) -> bool:
    return value
