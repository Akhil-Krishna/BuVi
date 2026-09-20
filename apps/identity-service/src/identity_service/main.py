"""identity-service application factory.

Owner: platform / identity. API contract: `contracts/openapi/identity-service.json`
(exported from Phase A2 onward). Health checks: `/health/live`, `/health/ready`.
Runbook: `docs/runbooks/identity-service.md`.

Three cross-cutting behaviours are installed here rather than repeated per
route: request correlation (Section 22), the single error envelope (Section 21),
and the authentication resolver that backs `platform_auth.get_principal`
(Section 7.2).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from identity_service.api.internal import router as internal_router
from identity_service.api.v1.health import router as health_router
from identity_service.api.v1.router import api_router
from identity_service.application.services.ports import IdentityEvents, UserLifecycle
from identity_service.core.config import Settings, get_settings
from identity_service.core.logging import configure_logging
from identity_service.dependencies import resolve_principal
from identity_service.infrastructure.db.session import create_engine, create_session_factory
from identity_service.infrastructure.email.sender import EmailSender, SmtpEmailSender
from identity_service.infrastructure.http.lifecycle import DashboardLifecycleClient
from identity_service.infrastructure.messaging.nats_events import (
    JetStreamIdentityEvents,
    UnavailableIdentityEvents,
)
from identity_service.infrastructure.oidc.client import KeycloakOidcClient, OidcClient
from identity_service.infrastructure.secrets.store import (
    InMemorySecretStore,
    SecretStore,
    VaultSecretStore,
)
from platform_auth import (
    ServiceTokenIssuer,
    ServiceTokenVerifier,
    install_principal_resolver,
    install_service_token_verifier,
)
from platform_observability import RequestIdMiddleware, docs_routes, install_error_handlers

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the process-wide resources, and tear them down on shutdown."""
    settings: Settings = app.state.settings
    settings.assert_production_safe()

    engine = create_engine(settings)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)

    http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    app.state.http = http

    if getattr(app.state, "secrets", None) is None:
        app.state.secrets = (
            InMemorySecretStore()
            if settings.vault_use_memory_stub
            else VaultSecretStore(
                addr=settings.vault_addr,
                token=settings.vault_token.get_secret_value(),
                mount=settings.vault_mount,
                http=http,
            )
        )
    if getattr(app.state, "oidc", None) is None:
        app.state.oidc = KeycloakOidcClient(settings, http)
    if getattr(app.state, "email", None) is None:
        app.state.email = SmtpEmailSender(settings)

    if getattr(app.state, "lifecycle", None) is None:
        app.state.lifecycle = DashboardLifecycleClient(
            base_url=settings.dashboard_url, http=http, issuer=app.state.service_token_issuer
        )

    publisher: JetStreamIdentityEvents | None = None
    if getattr(app.state, "events", None) is None:
        app.state.events = UnavailableIdentityEvents()
        if settings.events_enabled:
            try:
                publisher = await JetStreamIdentityEvents.connect(
                    settings.nats_url, stream=settings.events_stream
                )
                app.state.events = publisher
            except Exception as error:
                # Login and admin work never depend on the event stream; notifications do.
                logger.warning(
                    "identity event stream unavailable",
                    extra={"context": {"error_type": type(error).__name__}},
                )

    try:
        yield
    finally:
        if publisher is not None:
            await publisher.close()
        await http.aclose()
        await engine.dispose()


def create_app(
    *,
    settings: Settings | None = None,
    secrets: SecretStore | None = None,
    oidc: OidcClient | None = None,
    email: EmailSender | None = None,
    service_token_issuer: ServiceTokenIssuer | None = None,
    events: IdentityEvents | None = None,
    lifecycle: UserLifecycle | None = None,
) -> FastAPI:
    """Build the application.

    The adapter arguments exist so integration tests can substitute a stub OIDC
    provider or an in-memory mailbox without patching module globals. Production
    passes none of them.
    """
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    app = FastAPI(
        title="identity-service",
        version="0.1.0",
        description=(
            "Users, organizations, roles, invitations, sessions, MFA, API keys "
            "and the audit log for the BuVi platform."
        ),
        lifespan=lifespan,
        **docs_routes(resolved.environment),
    )
    app.state.settings = resolved
    app.state.secrets = secrets
    app.state.oidc = oidc
    app.state.email = email
    app.state.events = events
    app.state.lifecycle = lifecycle

    install_principal_resolver(app, resolve_principal)

    # Section 6.3: identity-service issues service tokens and verifies the ones
    # addressed to it against its own public key -- no network hop to itself.
    issuer = service_token_issuer or ServiceTokenIssuer(
        issuer=resolved.service_token_issuer,
        private_key_pem=(
            resolved.service_token_private_key.get_secret_value()
            if resolved.service_token_private_key
            else None
        ),
        key_id=resolved.service_token_key_id,
    )
    app.state.service_token_issuer = issuer
    install_service_token_verifier(
        app,
        ServiceTokenVerifier(
            issuer=resolved.service_token_issuer,
            audience=resolved.service_name,
            keyset=issuer.jwks(),
        ),
    )

    # Behind api-gateway: trust the forwarded request id (validated) so one request is
    # traceable across hops (Section 22). The shared handlers give the Section 21 envelope.
    app.add_middleware(RequestIdMiddleware, trust_inbound=True)
    install_error_handlers(app)

    app.include_router(health_router)
    app.include_router(api_router)
    app.include_router(internal_router)
    return app


def run() -> None:  # pragma: no cover - console entry point
    """`identity-service` console script: run the ASGI app with uvicorn."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "identity_service.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - bound inside a container, fronted by the gateway
        port=8001,
        log_level=settings.log_level.lower(),
    )
