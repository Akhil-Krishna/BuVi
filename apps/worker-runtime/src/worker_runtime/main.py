"""worker-runtime application factory.

Owner: platform / analytics. No public API: health probes only. Runbook:
`docs/runbooks/worker-runtime.md`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from platform_auth import ServiceTokenClient
from platform_observability import RequestIdMiddleware, install_error_handlers
from worker_runtime.api.v1.health import router as health_router
from worker_runtime.application.services.run_dispatcher import RunDispatcher
from worker_runtime.application.services.usage_aggregator import UsageAggregator
from worker_runtime.core.config import Settings, get_settings
from worker_runtime.core.logging import configure_logging
from worker_runtime.infrastructure.http.orchestrator_client import OrchestratorClient
from worker_runtime.infrastructure.messaging.jetstream_consumer import RunConsumer
from worker_runtime.infrastructure.messaging.usage_consumer import UsageConsumer

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    settings.assert_production_safe()
    http = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.execute_timeout_seconds, connect=3.0),
        transport=app.state.http_transport,
    )
    tokens = ServiceTokenClient(
        token_url=settings.service_token_url,
        client_id=settings.service_client_id,
        client_secret=settings.service_client_secret.get_secret_value(),
        http=http,
    )
    orchestrator = OrchestratorClient(
        base_url=settings.orchestrator_url,
        http=http,
        tokens=tokens,
        timeout_seconds=settings.execute_timeout_seconds,
    )
    consumer = RunConsumer(
        settings=settings,
        dispatcher=RunDispatcher(
            orchestrator=orchestrator,
            retry_base_seconds=settings.retry_base_seconds,
            max_deliveries=settings.max_deliver,
        ),
    )
    usage = UsageConsumer(
        settings=settings,
        aggregator=UsageAggregator(store=orchestrator, retry_seconds=settings.usage_retry_seconds),
    )
    app.state.consumer = consumer
    app.state.usage_consumer = usage
    tasks = [asyncio.create_task(consumer.run()), asyncio.create_task(usage.run())]
    try:
        yield
    finally:
        for running in (consumer, usage):
            await running.stop()
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await http.aclose()


def create_app(
    *, settings: Settings | None = None, http_transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    app = FastAPI(
        title="worker-runtime",
        version="0.1.0",
        description="Durable queue consumers (Section 18).",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.http_transport = http_transport
    app.state.consumer = None
    app.state.usage_consumer = None
    app.add_middleware(RequestIdMiddleware, trust_inbound=False)
    install_error_handlers(app)
    app.include_router(health_router)
    return app


def run() -> None:  # pragma: no cover - console entry point
    import uvicorn

    uvicorn.run(
        "worker_runtime.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container bind
        port=8005,
        log_level=get_settings().log_level.lower(),
    )
