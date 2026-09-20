"""mcp-gateway application factory.

Owner: platform / integrations. API: `/api/v1/mcp/*` via api-gateway; contract
`contracts/openapi/mcp-gateway.json`. Health: `/health/live`, `/health/ready`.
Runbook: `docs/runbooks/mcp-gateway.md`.

Two HTTP clients with different jobs: one for platform services (identity-service), and one for
MCP servers only, built by `infrastructure/mcp/egress.py` (no redirects, no keep-alive, no
environment proxy). Customer-directed traffic never shares the platform client.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from mcp_gateway.api.v1.health import router as health_router
from mcp_gateway.api.v1.router import api_router
from mcp_gateway.application.services.ports import McpEvents, McpServers
from mcp_gateway.core.config import Settings, get_settings
from mcp_gateway.core.logging import configure_logging
from mcp_gateway.dependencies import resolve_principal
from mcp_gateway.infrastructure.audit.sink import AuditSink, IdentityAuditSink
from mcp_gateway.infrastructure.db.session import create_engine, create_session_factory
from mcp_gateway.infrastructure.mcp.client import McpClient
from mcp_gateway.infrastructure.mcp.egress import (
    EgressClient,
    Resolver,
    UpstreamLimits,
    build_http_client,
)
from mcp_gateway.infrastructure.messaging.nats_events import JetStreamMcpEvents
from platform_auth import (
    IntrospectionClient,
    ServiceTokenClient,
    ServiceTokenVerifier,
    install_principal_resolver,
    install_service_token_verifier,
)
from platform_egress import EgressPolicy, resolve_host
from platform_observability import RequestIdMiddleware, docs_routes, install_error_handlers
from platform_secrets import InMemorySecretStore, SecretStore, VaultSecretStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state: Any = app.state
    settings: Settings = state.settings
    settings.assert_production_safe()
    engine = create_engine(settings)
    state.session_factory = create_session_factory(engine)
    http = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=3.0),
        follow_redirects=False,
        transport=state.http_transport,
    )
    tokens = ServiceTokenClient(
        token_url=settings.service_token_url,
        client_id=settings.service_client_id,
        client_secret=settings.service_client_secret.get_secret_value(),
        http=http,
    )
    state.introspection = IntrospectionClient(
        base_url=settings.identity_url, http=http, tokens=tokens
    )
    if state.service_token_verifier is None:
        install_service_token_verifier(
            app,
            ServiceTokenVerifier(
                issuer=settings.service_token_issuer,
                audience=settings.service_name,
                jwks_url=settings.jwks_url,
                http=http,
            ),
        )
    if state.audit is None:
        state.audit = IdentityAuditSink(identity=state.introspection, http=http)
    if state.secrets is None:
        state.secrets = (
            InMemorySecretStore()
            if settings.vault_use_memory_stub
            else VaultSecretStore(
                addr=settings.vault_addr,
                token=settings.vault_token.get_secret_value(),
                mount=settings.vault_mount,
                http=http,
            )
        )
    state.egress = EgressPolicy.from_hosts(settings.egress_allowed_internal_hosts)
    limits = UpstreamLimits(
        timeout_seconds=settings.upstream_timeout_seconds,
        connect_timeout_seconds=settings.upstream_connect_timeout_seconds,
        max_response_bytes=settings.max_response_bytes,
    )
    mcp_http = build_http_client(limits, state.mcp_transport)
    if state.mcp is None:
        state.mcp = McpClient(
            EgressClient(http=mcp_http, egress=state.egress, limits=limits, resolver=state.resolver)
        )
    publisher: JetStreamMcpEvents | None = None
    if state.events is None:
        try:
            publisher = await JetStreamMcpEvents.connect(
                settings.nats_url, stream=settings.mcp_stream
            )
            state.events = publisher
            state.events_ready = lambda: publisher.connected
        except Exception as error:
            # Denials are still recorded (row + audit) without the stream; readiness degrades.
            logger.warning(
                "mcp event stream unavailable",
                extra={"context": {"error_type": type(error).__name__}},
            )
            state.events = _UnavailableEvents()
            state.events_ready = lambda: False
    try:
        yield
    finally:
        if publisher is not None:
            await publisher.close()
        await mcp_http.aclose()
        await http.aclose()
        await engine.dispose()


class _UnavailableEvents:
    async def invocation_denied(self, event: object) -> None:  # noqa: ARG002 - always unavailable
        raise ConnectionError("event stream unavailable")


def create_app(
    *,
    settings: Settings | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
    service_token_verifier: ServiceTokenVerifier | None = None,
    secrets: SecretStore | None = None,
    audit: AuditSink | None = None,
    events: McpEvents | None = None,
    mcp: McpServers | None = None,
    mcp_transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver = resolve_host,
) -> FastAPI:
    """Tests inject fakes at the edges: identity over `http_transport`, and the MCP wire over
    `mcp_transport` + `resolver`, so the real client and egress checks still run."""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    app = FastAPI(
        title="mcp-gateway",
        version="0.1.0",
        description="Governed MCP integrations: registry, tool policy, SSRF-safe invocation "
        "(Sections 8.7, 14, 15).",
        lifespan=lifespan,
        **docs_routes(resolved.environment),
    )
    state: Any = app.state
    state.settings = resolved
    state.http_transport = http_transport
    state.service_token_verifier = service_token_verifier
    state.secrets = secrets
    state.audit = audit
    state.events = events
    state.events_ready = lambda: True
    state.mcp = mcp
    state.mcp_transport = mcp_transport
    state.resolver = resolver
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
        "mcp_gateway.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container bind
        port=8009,
        log_level=get_settings().log_level.lower(),
    )
