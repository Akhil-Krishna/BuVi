"""metadata-service application factory.

Owner: platform / data. API contract: `contracts/openapi/metadata-service.json`.
Health: `/health/live`, `/health/ready`. Runbook: `docs/runbooks/metadata-service.md`.

Owns data-source connection metadata (never secret plaintext at rest), the schema
catalog and catalog sync (Sections 3, 8.2, 13.1). Reached only through api-gateway.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from metadata_service.api.v1.health import router as health_router
from metadata_service.api.v1.router import api_router
from metadata_service.core.config import Settings, get_settings
from metadata_service.core.logging import configure_logging
from metadata_service.dependencies import resolve_principal
from metadata_service.domain.policies.egress import EgressPolicy
from metadata_service.infrastructure.audit.sink import IdentityAuditSink
from metadata_service.infrastructure.connectors.base import CatalogConnector, ConnectorLimits
from metadata_service.infrastructure.connectors.postgres import PostgresCatalogConnector
from metadata_service.infrastructure.db.session import create_engine, create_session_factory
from metadata_service.infrastructure.secrets.store import build_secret_store
from platform_auth import (
    IntrospectionClient,
    ServiceTokenClient,
    ServiceTokenVerifier,
    install_principal_resolver,
    install_service_token_verifier,
)
from platform_observability import RequestIdMiddleware, install_error_handlers
from platform_secrets import SecretStore


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
    identity = IntrospectionClient(base_url=settings.identity_url, http=http, tokens=tokens)
    app.state.identity = identity
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
    if app.state.secrets is None:
        app.state.secrets = build_secret_store(settings, http)
    app.state.audit = IdentityAuditSink(
        identity=identity, http=http, attempts=settings.audit_delivery_attempts
    )
    app.state.egress = EgressPolicy.from_hosts(settings.connector_allowed_internal_hosts)
    if app.state.connectors is None:
        app.state.connectors = {
            "postgres": PostgresCatalogConnector(
                egress=app.state.egress,
                limits=ConnectorLimits(
                    connect_timeout_seconds=settings.connector_connect_timeout_seconds,
                    statement_timeout_ms=settings.connector_statement_timeout_ms,
                    max_tables=settings.catalog_max_tables,
                    max_columns=settings.catalog_max_columns,
                ),
            )
        }
    try:
        yield
    finally:
        await http.aclose()
        await engine.dispose()


def create_app(
    *,
    settings: Settings | None = None,
    secrets: SecretStore | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
    service_token_verifier: ServiceTokenVerifier | None = None,
    connectors: Mapping[str, CatalogConnector] | None = None,
) -> FastAPI:
    """Build the application. Tests inject a fake identity-service transport, an
    in-memory secret store and a local service-token verifier; production passes none."""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    app = FastAPI(
        title="metadata-service",
        version="0.1.0",
        description=(
            "Data-source connection metadata, the schema catalog and catalog sync "
            "for the BuVi platform (build spec Sections 8.2, 13.1)."
        ),
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.secrets = secrets
    app.state.http_transport = http_transport
    app.state.connectors = connectors
    app.state.service_token_verifier = service_token_verifier
    if service_token_verifier is not None:
        install_service_token_verifier(app, service_token_verifier)

    install_principal_resolver(app, resolve_principal)
    app.add_middleware(RequestIdMiddleware, trust_inbound=True)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router)
    return app


def run() -> None:  # pragma: no cover - console entry point
    import uvicorn

    uvicorn.run(
        "metadata_service.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - bound inside a container, fronted by the gateway
        port=8002,
        log_level=get_settings().log_level.lower(),
    )
