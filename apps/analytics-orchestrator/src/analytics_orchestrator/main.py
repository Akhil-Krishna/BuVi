"""analytics-orchestrator application factory.

Owner: platform / analytics. API: `/api/v1` chat routes via api-gateway; internal execute and
events routes; contract `contracts/openapi/analytics-orchestrator.json`. Health: `/health/live`,
`/health/ready`. Runbook: `docs/runbooks/analytics-orchestrator.md`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from analytics_orchestrator.api.internal import router as internal_router
from analytics_orchestrator.api.v1.health import router as health_router
from analytics_orchestrator.api.v1.router import api_router
from analytics_orchestrator.application.services.model_router import (
    BudgetLimits,
    ModelRoute,
    ModelRouter,
)
from analytics_orchestrator.application.services.ports import ModelProvider, RunQueue, UsageSink
from analytics_orchestrator.application.services.run_executor import (
    AfterStep,
    FlowLimits,
    RunExecutor,
)
from analytics_orchestrator.core.config import Settings, get_settings
from analytics_orchestrator.core.logging import configure_logging
from analytics_orchestrator.dependencies import resolve_principal
from analytics_orchestrator.infrastructure.cache.token_ledger import RedisTokenLedger
from analytics_orchestrator.infrastructure.db.run_lock import run_lock
from analytics_orchestrator.infrastructure.db.session import create_engine, create_session_factory
from analytics_orchestrator.infrastructure.flow.analytics_flow import (
    CrewAiFlowRunner,
    silence_crewai_console,
)
from analytics_orchestrator.infrastructure.http.clients import (
    DashboardClient,
    IdentityClient,
    MetadataClient,
    QueryGatewayClient,
    SemanticClient,
    VisualizationClient,
)
from analytics_orchestrator.infrastructure.llm.scripted_provider import ScriptedProvider
from analytics_orchestrator.infrastructure.messaging.nats_queue import (
    JetStreamPublisher,
    ObservedUsageSink,
)
from analytics_orchestrator.infrastructure.messaging.redis_events import RedisRunEventPublisher
from platform_auth import (
    IntrospectionClient,
    ServiceTokenClient,
    ServiceTokenVerifier,
    install_principal_resolver,
    install_service_token_verifier,
)
from platform_observability import RequestIdMiddleware, docs_routes, install_error_handlers


def _provider(settings: Settings, injected: ModelProvider | None) -> ModelProvider:
    if injected is not None:
        return injected
    if settings.llm_provider == "anthropic":
        from analytics_orchestrator.infrastructure.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(timeout_seconds=settings.stage_timeout_seconds)
    if settings.llm_provider == "openai_compatible":
        from analytics_orchestrator.infrastructure.llm.openai_compatible_provider import (
            OpenAICompatibleProvider,
        )

        if not settings.llm_base_url:
            raise RuntimeError("llm_provider is openai_compatible but llm_base_url is unset")
        return OpenAICompatibleProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            timeout_seconds=settings.stage_timeout_seconds,
            response_format_mode=settings.llm_response_format,
        )
    return ScriptedProvider(latency_seconds=settings.scripted_latency_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    settings.assert_production_safe()
    silence_crewai_console()
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
    app.state.identity = IdentityClient(base_url=settings.identity_url, http=http, tokens=tokens)
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
    redis = Redis.from_url(settings.redis_url, socket_timeout=2.0, socket_connect_timeout=2.0)
    app.state.redis = redis
    app.state.token_ledger = RedisTokenLedger(redis)
    app.state.events = RedisRunEventPublisher(redis)

    publisher: JetStreamPublisher | None = None
    if app.state.queue is None:
        publisher = await JetStreamPublisher.connect(
            settings.nats_url,
            run_stream=settings.run_stream,
            billing_stream=settings.billing_stream,
        )
        app.state.queue = publisher
        app.state.queue_ready = lambda: publisher.connected
    else:
        app.state.queue_ready = lambda: True
    usage = ObservedUsageSink(app.state.usage_sink or app.state.queue)
    app.state.usage = usage

    provider = _provider(settings, app.state.model_provider)
    app.state.model_provider = provider
    router = ModelRouter(
        primary=ModelRoute(provider, settings.llm_model),
        fallback=ModelRoute(provider, settings.llm_fallback_model)
        if settings.llm_fallback_model
        else None,
        ledger=app.state.token_ledger,
        usage=usage,
        limits=BudgetLimits(
            run_tokens=settings.run_token_budget,
            tenant_daily_tokens=settings.tenant_daily_token_budget,
            max_tokens_per_call=settings.llm_max_tokens_per_call,
            max_repairs=settings.max_repair_attempts,
        ),
    )
    app.state.executor = RunExecutor(
        session_factory=app.state.session_factory,
        lock=run_lock(engine),
        flow=CrewAiFlowRunner(),
        router=router,
        identity=app.state.identity,
        metadata=MetadataClient(base_url=settings.metadata_url, http=http, tokens=tokens),
        queries=QueryGatewayClient(base_url=settings.query_gateway_url, http=http, tokens=tokens),
        charts=VisualizationClient(base_url=settings.visualization_url, http=http, tokens=tokens),
        artifacts=DashboardClient(base_url=settings.dashboard_url, http=http, tokens=tokens),
        semantics=SemanticClient(base_url=settings.semantic_url, http=http, tokens=tokens),
        events=app.state.events,
        limits=FlowLimits(
            stage_timeout_seconds=settings.stage_timeout_seconds,
            run_timeout_seconds=settings.run_timeout_seconds,
            query_max_rows=settings.query_max_rows,
            query_timeout_ms=settings.query_timeout_ms,
            context_max_tables=settings.context_max_tables,
            max_repairs=settings.max_repair_attempts,
            query_capacity_retries=settings.query_capacity_retries,
            query_capacity_backoff_seconds=settings.query_capacity_backoff_seconds,
        ),
        after_step=app.state.after_step,
    )
    try:
        yield
    finally:
        if publisher is not None:
            await publisher.close()
        await redis.aclose()
        await http.aclose()
        await engine.dispose()


def create_app(
    *,
    settings: Settings | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
    service_token_verifier: ServiceTokenVerifier | None = None,
    queue: RunQueue | None = None,
    usage_sink: UsageSink | None = None,
    model_provider: ModelProvider | None = None,
    after_step: AfterStep | None = None,
) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    app = FastAPI(
        title="analytics-orchestrator",
        version="0.1.0",
        description="Conversations, analytics runs and the CrewAI AnalyticsFlow (Sections 10, 23).",
        lifespan=lifespan,
        **docs_routes(resolved.environment),
    )
    state: Any = app.state
    state.settings = resolved
    state.http_transport = http_transport
    state.queue = queue
    state.usage_sink = usage_sink
    state.model_provider = model_provider
    state.after_step = after_step
    state.service_token_verifier = service_token_verifier
    state.queue_ready = lambda: True
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
        "analytics_orchestrator.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container bind
        port=8004,
        log_level=get_settings().log_level.lower(),
    )
