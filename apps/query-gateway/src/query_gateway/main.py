"""query-gateway application factory.

Owner: platform / data security. API: `POST /internal/v1/queries` (service-to-service only;
contract `contracts/openapi/query-gateway.json`). Health: `/health/live`, `/health/ready`.
Runbook: `docs/runbooks/query-gateway.md`. The only service that executes arbitrary or
business SQL with customer credentials (Section 13).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from platform_auth import (
    IntrospectionClient,
    ServiceTokenClient,
    ServiceTokenVerifier,
    install_principal_resolver,
    install_service_token_verifier,
)
from platform_egress import EgressPolicy
from platform_observability import RequestIdMiddleware, install_error_handlers
from platform_secrets import InMemorySecretStore, SecretStore, VaultSecretStore
from query_gateway.api.internal import router as internal_router
from query_gateway.api.v1.health import router as health_router
from query_gateway.core.config import Settings, get_settings
from query_gateway.core.logging import configure_logging
from query_gateway.dependencies import resolve_principal
from query_gateway.domain.policies.sql_validator import SqlValidator
from query_gateway.infrastructure.cache.tenant_concurrency import TenantConcurrencyLimiter
from query_gateway.infrastructure.connectors.base import QueryExecutor
from query_gateway.infrastructure.connectors.postgres import PostgresQueryExecutor
from query_gateway.infrastructure.db.session import create_engine, create_session_factory
from query_gateway.infrastructure.http.identity_resolver import IdentityResolver
from query_gateway.infrastructure.http.metadata_client import MetadataPolicyClient
from query_gateway.infrastructure.storage.base import InMemoryResultStore, ResultStore
from query_gateway.infrastructure.storage.minio_store import MinioResultStore


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
    app.state.identity = IntrospectionClient(
        base_url=settings.identity_url, http=http, tokens=tokens
    )
    app.state.identity_resolver = IdentityResolver(identity=app.state.identity, http=http)
    app.state.policies = MetadataPolicyClient(
        base_url=settings.metadata_url,
        http=http,
        tokens=tokens,
        ttl_seconds=settings.policy_cache_ttl_seconds,
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
    if app.state.secrets is None:
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
    if app.state.results is None:
        app.state.results = (
            InMemoryResultStore(settings.result_ttl_days)
            if settings.result_store_use_memory_stub
            else MinioResultStore(
                endpoint=settings.result_store_endpoint,
                access_key=settings.result_store_access_key.get_secret_value(),
                secret_key=settings.result_store_secret_key.get_secret_value(),
                secure=settings.result_store_secure,
                bucket=settings.result_store_bucket,
                ttl_days=settings.result_ttl_days,
            )
        )
    owned_executors = app.state.executors is None
    if owned_executors:
        app.state.executors = {
            "postgres": PostgresQueryExecutor(
                egress=EgressPolicy.from_hosts(settings.connector_allowed_internal_hosts),
                connect_timeout_seconds=settings.connector_connect_timeout_seconds,
                pool_max_size=settings.connector_pool_max_size,
                pool_idle_seconds=settings.connector_pool_idle_seconds,
            )
        }
    app.state.limiter = TenantConcurrencyLimiter(settings.tenant_max_concurrent_queries)
    app.state.validator = SqlValidator(max_length=settings.max_sql_length)
    try:
        yield
    finally:
        if owned_executors:
            for executor in app.state.executors.values():
                await executor.close()
        await http.aclose()
        await engine.dispose()


def create_app(
    *,
    settings: Settings | None = None,
    secrets: SecretStore | None = None,
    results: ResultStore | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
    service_token_verifier: ServiceTokenVerifier | None = None,
    executors: Mapping[str, QueryExecutor] | None = None,
) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    app = FastAPI(
        title="query-gateway",
        version="0.1.0",
        description=(
            "SQL validation, read-only execution, result handles and query audit (Section 13)."
        ),
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.secrets = secrets
    app.state.results = results
    app.state.http_transport = http_transport
    app.state.executors = executors
    app.state.service_token_verifier = service_token_verifier
    if service_token_verifier is not None:
        install_service_token_verifier(app, service_token_verifier)
    install_principal_resolver(app, resolve_principal)
    app.add_middleware(RequestIdMiddleware, trust_inbound=True)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(internal_router)
    return app


def run() -> None:  # pragma: no cover - console entry point
    import uvicorn

    uvicorn.run(
        "query_gateway.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container bind
        port=8003,
        log_level=get_settings().log_level.lower(),
    )
