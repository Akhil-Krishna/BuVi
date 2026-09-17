"""visualization-service application factory.

Owner: platform / visualization. API: `POST /internal/v1/chart-specs/validate` only; contract
`contracts/openapi/visualization-service.json`. Health: `/health/live`, `/health/ready`.
Runbook: `docs/runbooks/visualization-service.md`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from platform_auth import ServiceTokenVerifier, install_service_token_verifier
from platform_observability import RequestIdMiddleware, install_error_handlers
from visualization_service.api.internal import router as internal_router
from visualization_service.api.v1.health import router as health_router
from visualization_service.core.config import Settings, get_settings
from visualization_service.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    http = httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0), follow_redirects=False)
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
    try:
        yield
    finally:
        await http.aclose()


def create_app(
    *, settings: Settings | None = None, service_token_verifier: ServiceTokenVerifier | None = None
) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    app = FastAPI(
        title="visualization-service",
        version="0.1.0",
        description="ChartSpec validation and render-safety rules (Section 17).",
        lifespan=lifespan,
    )
    state: Any = app.state
    state.settings = resolved
    state.service_token_verifier = service_token_verifier
    if service_token_verifier is not None:
        install_service_token_verifier(app, service_token_verifier)
    app.add_middleware(RequestIdMiddleware, trust_inbound=True)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(internal_router)
    return app


def run() -> None:  # pragma: no cover - console entry point
    import uvicorn

    uvicorn.run(
        "visualization_service.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container bind
        port=8006,
        log_level=get_settings().log_level.lower(),
    )
