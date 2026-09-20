"""semantic-service application factory.

Owner: platform / semantic layer. API: `/api/v1/semantic/*` via api-gateway; internal
`/internal/v1/semantic-context` for the Flow; contract `contracts/openapi/semantic-service.json`.
Health: `/health/live`, `/health/ready`. Runbook: `docs/runbooks/semantic-service.md`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from platform_auth import (
    IntrospectionClient,
    ServiceTokenClient,
    ServiceTokenVerifier,
    install_principal_resolver,
    install_service_token_verifier,
)
from platform_observability import RequestIdMiddleware, docs_routes, install_error_handlers
from semantic_service.api.internal import router as internal_router
from semantic_service.api.v1.health import router as health_router
from semantic_service.api.v1.router import api_router
from semantic_service.application.services.definitions import Catalog
from semantic_service.core.config import Settings, get_settings
from semantic_service.core.logging import configure_logging
from semantic_service.dependencies import resolve_principal
from semantic_service.infrastructure.audit.sink import AuditSink, IdentityAuditSink
from semantic_service.infrastructure.db.session import create_engine, create_session_factory
from semantic_service.infrastructure.http.catalog_client import MetadataCatalogClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    settings.assert_production_safe()
    engine = create_engine(settings)
    app.state.session_factory = create_session_factory(engine)
    http = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=3.0),
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
    if app.state.catalog is None:
        app.state.catalog = MetadataCatalogClient(
            base_url=settings.metadata_url, http=http, tokens=tokens
        )
    if app.state.audit is None:
        app.state.audit = IdentityAuditSink(identity=app.state.introspection, http=http)
    try:
        yield
    finally:
        await http.aclose()
        await engine.dispose()


def create_app(
    *,
    settings: Settings | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
    service_token_verifier: ServiceTokenVerifier | None = None,
    catalog: Catalog | None = None,
    audit: AuditSink | None = None,
) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    app = FastAPI(
        title="semantic-service",
        version="0.1.0",
        description="Approved metrics and dimensions: the semantic layer (Sections 8.3, 12).",
        lifespan=lifespan,
        **docs_routes(resolved.environment),
    )
    state: Any = app.state
    state.settings = resolved
    state.http_transport = http_transport
    state.service_token_verifier = service_token_verifier
    state.catalog = catalog
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
        "semantic_service.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container bind
        port=8008,
        log_level=get_settings().log_level.lower(),
    )
