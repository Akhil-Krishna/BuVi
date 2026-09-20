"""dashboard-service application factory.

Owner: platform / dashboards. API: `/api/v1` artifact and dashboard routes via api-gateway; internal
artifact store route; contract `contracts/openapi/dashboard-service.json`. Health: `/health/live`,
`/health/ready`. Runbook: `docs/runbooks/dashboard-service.md`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from dashboard_service.api.internal import router as internal_router
from dashboard_service.api.v1.health import router as health_router
from dashboard_service.api.v1.router import api_router
from dashboard_service.application.services.ports import (
    ChartValidator,
    DashboardEvents,
    ResultReader,
)
from dashboard_service.core.config import Settings, get_settings
from dashboard_service.core.logging import configure_logging
from dashboard_service.dependencies import resolve_principal
from dashboard_service.infrastructure.audit.sink import AuditSink, IdentityAuditSink
from dashboard_service.infrastructure.db.session import create_engine, create_session_factory
from dashboard_service.infrastructure.http.clients import QueryResultsClient, VisualizationClient
from dashboard_service.infrastructure.messaging.nats_events import JetStreamDashboardEvents
from platform_auth import (
    IntrospectionClient,
    ServiceTokenClient,
    ServiceTokenVerifier,
    install_principal_resolver,
    install_service_token_verifier,
)
from platform_observability import RequestIdMiddleware, docs_routes, install_error_handlers

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    settings.assert_production_safe()
    engine = create_engine(settings)
    app.state.session_factory = create_session_factory(engine)
    http = httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=3.0),
        follow_redirects=False,
        transport=app.state.http_transport,
    )
    tokens = ServiceTokenClient(
        token_url=settings.service_token_url,
        client_id=settings.service_client_id,
        client_secret=settings.service_client_secret.get_secret_value(),
        http=http,
    )
    app.state.introspection = IntrospectionClient(
        base_url=settings.identity_url, http=http, tokens=tokens
    )
    if app.state.service_token_verifier is None:
        install_service_token_verifier(
            app,
            ServiceTokenVerifier(
                issuer=settings.service_token_issuer,
                audience=settings.service_name,
                jwks_url=settings.jwks_url,
                http=http,
            ),
        )
    if app.state.audit is None:
        app.state.audit = IdentityAuditSink(identity=app.state.introspection, http=http)
    if app.state.visualization is None:
        app.state.visualization = VisualizationClient(
            base_url=settings.visualization_url, http=http, tokens=tokens
        )
    if app.state.results is None:
        app.state.results = QueryResultsClient(
            base_url=settings.query_gateway_url, http=http, tokens=tokens
        )
    publisher: JetStreamDashboardEvents | None = None
    if app.state.events is None:
        try:
            publisher = await JetStreamDashboardEvents.connect(
                settings.nats_url, stream=settings.dashboard_stream
            )
            app.state.events = publisher
            app.state.events_ready = lambda: publisher.connected
        except Exception as error:
            # Pins still work without the event stream; readiness reports `degraded`.
            logger.warning(
                "dashboard event stream unavailable",
                extra={"context": {"error_type": type(error).__name__}},
            )
            app.state.events = _UnavailableEvents()
            app.state.events_ready = lambda: False
    try:
        yield
    finally:
        if publisher is not None:
            await publisher.close()
        await http.aclose()
        await engine.dispose()


class _UnavailableEvents:
    async def tile_pinned(self, event: object) -> None:  # noqa: ARG002 - always unavailable
        raise ConnectionError("event stream unavailable")


def create_app(
    *,
    settings: Settings | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
    service_token_verifier: ServiceTokenVerifier | None = None,
    visualization: ChartValidator | None = None,
    results: ResultReader | None = None,
    events: DashboardEvents | None = None,
    audit: AuditSink | None = None,
) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    app = FastAPI(
        title="dashboard-service",
        version="0.1.0",
        description="Canonical artifact store, dashboards and tiles (Sections 8.6, 16).",
        lifespan=lifespan,
        **docs_routes(resolved.environment),
    )
    state: Any = app.state
    state.settings = resolved
    state.http_transport = http_transport
    state.service_token_verifier = service_token_verifier
    state.visualization = visualization
    state.results = results
    state.events = events
    state.events_ready = lambda: True
    state.audit = audit
    if service_token_verifier is not None:
        install_service_token_verifier(app, service_token_verifier)
    install_principal_resolver(app, resolve_principal)
    app.add_middleware(RequestIdMiddleware, trust_inbound=True)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router)
    app.include_router(internal_router)
    return app


def run() -> None:  # pragma: no cover - console entry point
    import uvicorn

    uvicorn.run(
        "dashboard_service.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container bind
        port=8007,
        log_level=get_settings().log_level.lower(),
    )
