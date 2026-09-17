"""worker-runtime against a real NATS JetStream (Sections 10.2, 18.1): a finished run is acked;
a worker that dies mid-run leaves the message unacked and JetStream redelivers it."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager

import httpx
import nats
import pytest
from fastapi import FastAPI

from platform_contracts import RunRequested
from worker_runtime.core.config import RUN_REQUESTED_SUBJECT, Settings
from worker_runtime.main import create_app

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


class Orchestrator:
    def __init__(self) -> None:
        self.executions: list[str] = []
        self.status = 200
        self.block: asyncio.Event | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/v1/oauth/token":
            form = dict(httpx.QueryParams(request.content.decode()))
            assert (form["client_id"], form["audience"], form["scope"]) == (
                "worker-runtime",
                "analytics-orchestrator",
                "analytics-orchestrator:execute",
            )
            return httpx.Response(200, json={"access_token": "svc-token", "expires_in": 300})
        if request.url.path.endswith("/execute"):
            assert request.headers["x-service-authorization"] == "Bearer svc-token"
            self.executions.append(request.url.path.split("/")[4])
            return httpx.Response(self.status, json={"status": "completed"})
        return httpx.Response(404)


async def _publish(url: str, stream: str) -> RunRequested:
    message = RunRequested(
        run_id=uuid.uuid4(), tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4()
    )
    client = await nats.connect(url)
    js = client.jetstream()
    await js.publish(RUN_REQUESTED_SUBJECT, message.model_dump_json().encode(), stream=stream)
    await client.close()
    return message


async def _wait_for(predicate: Callable[[], bool], wait_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + wait_seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.1)


async def _pending(url: str, stream: str, durable: str) -> tuple[int, int]:
    client = await nats.connect(url)
    info = await client.jetstream().consumer_info(stream, durable)
    await client.close()
    return info.num_pending, info.num_ack_pending


def _settings(url: str, suffix: str, **overrides: object) -> Settings:
    return Settings(
        environment="test",
        nats_url=url,
        run_stream="ANALYTICS",
        durable_name=f"worker-{suffix}",
        fetch_timeout_seconds=0.5,
        heartbeat_seconds=0.5,
        retry_base_seconds=0.2,
        log_level="WARNING",
        **overrides,
    )  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
async def _fresh_stream(nats_url: str) -> None:
    """Each test starts with no ANALYTICS stream, so no leftover messages or consumers."""
    client = await nats.connect(nats_url)
    with contextlib.suppress(Exception):  # absent on the first test
        await client.jetstream().delete_stream("ANALYTICS")
    await client.close()


@asynccontextmanager
async def _started(settings: Settings, orchestrator: Orchestrator) -> AsyncIterator[FastAPI]:
    app = create_app(settings=settings, http_transport=httpx.MockTransport(orchestrator.handler))
    async with app.router.lifespan_context(app):
        consumer = app.state.consumer
        await _wait_for(lambda: bool(consumer.connected))
        yield app


async def test_finished_run_is_executed_once_and_acked(nats_url: str) -> None:
    orchestrator = Orchestrator()
    settings = _settings(nats_url, "ack")
    async with _started(settings, orchestrator) as app:
        consumer = app.state.consumer
        message = await _publish(nats_url, settings.run_stream)
        await _wait_for(lambda: consumer.handled == 1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://w"
        ) as client:
            ready = await client.get("/health/ready")
        assert ready.status_code == 200
    assert orchestrator.executions == [str(message.run_id)]
    assert await _pending(nats_url, settings.run_stream, settings.durable_name) == (0, 0)


async def test_a_worker_that_dies_mid_run_leaves_the_run_for_redelivery(nats_url: str) -> None:
    orchestrator = Orchestrator()
    settings = _settings(nats_url, "crash", ack_wait_seconds=2.0)
    async with _started(settings, orchestrator) as app:
        dispatcher = app.state.consumer._dispatcher
        original = dispatcher.handle

        async def hang_after_call(
            data: bytes, *, delivery_count: int, _original: Callable[..., object] = original
        ) -> object:
            await _original(data, delivery_count=delivery_count)  # type: ignore[misc]
            await (
                asyncio.Event().wait()
            )  # the worker dies here: no ack, and heartbeats stop on shutdown
            return None

        dispatcher.handle = hang_after_call
        message = await _publish(nats_url, settings.run_stream)
        await _wait_for(lambda: len(orchestrator.executions) == 1)
    # A fresh worker on the same durable consumer receives the redelivery after ack_wait.
    async with _started(settings, orchestrator) as app:
        consumer = app.state.consumer
        await _wait_for(lambda: consumer.handled == 1, wait_seconds=20)
    assert orchestrator.executions == [str(message.run_id), str(message.run_id)]
    assert await _pending(nats_url, settings.run_stream, settings.durable_name) == (0, 0)


async def test_busy_run_is_retried_until_the_orchestrator_answers(nats_url: str) -> None:
    orchestrator = Orchestrator()
    orchestrator.status = 409
    settings = _settings(nats_url, "busy")
    async with _started(settings, orchestrator) as app:
        consumer = app.state.consumer
        await _publish(nats_url, settings.run_stream)
        await _wait_for(lambda: consumer.handled >= 1)
        orchestrator.status = 200
        await _wait_for(lambda: consumer.handled >= 2, wait_seconds=30)
    assert len(orchestrator.executions) >= 2
    assert await _pending(nats_url, settings.run_stream, settings.durable_name) == (0, 0)
