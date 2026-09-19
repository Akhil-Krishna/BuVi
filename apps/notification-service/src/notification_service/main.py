"""notification-service application factory.

Owner: platform / integrations. API: `/api/v1/me/notifications*` and `/api/v1/admin/webhooks*`
via api-gateway; contract `contracts/openapi/notification-service.json`. Consumes the Section 18.1
topics that list it. Health: `/health/live`, `/health/ready`. Runbook:
`docs/runbooks/notification-service.md`.

Two HTTP clients with different jobs: one for platform services (identity-service), and one for
webhook receivers only (no redirects, no keep-alive, no environment proxy). Customer-directed
traffic never shares the platform client.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from notification_service.api.v1.health import router as health_router
from notification_service.api.v1.router import api_router
from notification_service.application.services.dispatcher import NotificationDispatcher
from notification_service.application.services.ports import (
    Directory,
    EmailSender,
)
from notification_service.core.config import Settings, get_settings
from notification_service.core.logging import configure_logging
from notification_service.dependencies import resolve_principal
from notification_service.domain.policies.routing import TOPICS
from notification_service.infrastructure.audit.sink import AuditSink, IdentityAuditSink
from notification_service.infrastructure.db.repositories.notification_repository import (
    NotificationRepository,
)
from notification_service.infrastructure.db.session import (
    create_engine,
    create_session_factory,
    tenant_scope,
)
from notification_service.infrastructure.email.smtp import SmtpEmailSender
from notification_service.infrastructure.http.directory_client import IdentityDirectory
from notification_service.infrastructure.messaging.consumer import TopicConsumer
from notification_service.infrastructure.webhooks.delivery import (
    PinnedWebhookSender,
    build_webhook_client,
)
from platform_auth import (
    IntrospectionClient,
    ServiceTokenClient,
    ServiceTokenVerifier,
    install_principal_resolver,
    install_service_token_verifier,
)
from platform_egress import EgressPolicy, Resolver, resolve_host
from platform_observability import RequestIdMiddleware, install_error_handlers
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
    webhook_http = build_webhook_client(
        timeout_seconds=settings.webhook_timeout_seconds,
        connect_timeout_seconds=settings.webhook_connect_timeout_seconds,
        transport=state.webhook_transport,
    )
    factory = state.session_factory

    @asynccontextmanager
    async def scope(tenant_id: uuid.UUID) -> AsyncIterator[NotificationRepository]:
        async with tenant_scope(factory, tenant_id) as db:
            yield NotificationRepository(db)

    state.dispatcher = NotificationDispatcher(
        scope=scope,
        directory=state.directory or IdentityDirectory(identity=state.introspection, http=http),
        email=state.email or SmtpEmailSender(settings),
        webhooks=PinnedWebhookSender(
            http=webhook_http,
            egress=state.egress,
            attempts=settings.webhook_attempts,
            backoff_seconds=settings.webhook_backoff_seconds,
            resolver=state.resolver,
        ),
        secrets=state.secrets,
    )
    state.consumers = []
    tasks: list[asyncio.Task[None]] = []
    if settings.consumers_enabled:
        state.consumers = [
            TopicConsumer(settings=settings, dispatcher=state.dispatcher, subject=subject)
            for subject in TOPICS
        ]
        tasks = [asyncio.create_task(c.run()) for c in state.consumers]
    try:
        yield
    finally:
        for consumer in state.consumers:
            await consumer.stop()
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await webhook_http.aclose()
        await http.aclose()
        await engine.dispose()


def create_app(
    *,
    settings: Settings | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
    service_token_verifier: ServiceTokenVerifier | None = None,
    secrets: SecretStore | None = None,
    audit: AuditSink | None = None,
    directory: Directory | None = None,
    email: EmailSender | None = None,
    webhook_transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver = resolve_host,
) -> FastAPI:
    """Tests inject fakes at the edges: identity over `http_transport`, the webhook wire over
    `webhook_transport` + `resolver` (so the real egress checks and pinning still run)."""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    app = FastAPI(
        title="notification-service",
        version="0.1.0",
        description="In-app, email and signed-webhook notifications from platform events "
        "(Sections 8.8, 15, 18.1).",
        lifespan=lifespan,
    )
    state: Any = app.state
    state.settings = resolved
    state.http_transport = http_transport
    state.service_token_verifier = service_token_verifier
    state.secrets = secrets
    state.audit = audit
    state.directory = directory
    state.email = email
    state.webhook_transport = webhook_transport
    state.resolver = resolver
    state.consumers = []
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
        "notification_service.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container bind
        port=8010,
        log_level=get_settings().log_level.lower(),
    )
