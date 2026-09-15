"""api-gateway application factory.

Owner: platform / edge. API contract: `contracts/openapi/api-gateway.json`.
Health: `/health/live`, `/health/ready`. Runbook: `docs/runbooks/api-gateway.md`.

The only service the Next.js BFF calls (Section 3). Stateless; Redis holds only
rate-limit counters.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from redis.asyncio import Redis

from api_gateway.api.v1.health import router as health_router
from api_gateway.api.v1.routes import build_router
from api_gateway.core.config import Settings, get_settings
from api_gateway.core.logging import configure_logging
from api_gateway.domain.catalog import CATALOG
from api_gateway.infrastructure.cache.rate_limiter import RateLimiter, RedisRateLimiter
from api_gateway.infrastructure.http.identity_client import IdentityClient
from api_gateway.infrastructure.http.proxy import UpstreamProxy
from platform_auth import ServiceTokenClient
from platform_observability import RequestIdMiddleware, install_error_handlers

#: Repo-level `contracts/openapi/`, used to compose downstream request/response schemas.
DEFAULT_CONTRACTS_DIR = Path(__file__).resolve().parents[4] / "contracts" / "openapi"

ERROR_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["code", "message"],
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "request_id": {"type": ["string", "null"]},
                "details": {"type": "object"},
            },
        }
    },
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    settings.assert_production_safe()
    http = httpx.AsyncClient(
        timeout=httpx.Timeout(
            settings.upstream_timeout_seconds, connect=settings.upstream_connect_timeout_seconds
        ),
        follow_redirects=False,
        transport=app.state.http_transport,
    )
    tokens = ServiceTokenClient(
        token_url=settings.service_token_url,
        client_id=settings.service_client_id,
        client_secret=settings.service_client_secret.get_secret_value(),
        http=http,
    )
    app.state.identity = IdentityClient(
        base_url=settings.backend_urls["identity-service"], http=http, tokens=tokens
    )
    app.state.proxy = UpstreamProxy(backends=settings.backend_urls, http=http, tokens=tokens)
    redis: Redis | None = None
    if app.state.rate_limiter is None:
        redis = Redis.from_url(settings.redis_url, socket_timeout=1.0, socket_connect_timeout=1.0)
        app.state.rate_limiter = RedisRateLimiter(redis, fail_open=settings.rate_limit_fail_open)
    try:
        yield
    finally:
        await http.aclose()
        if redis is not None:
            await redis.aclose()


def _normalise(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def compose_openapi(app: FastAPI, contracts_dir: Path | None) -> dict[str, Any]:
    """Gateway OpenAPI, with request/response bodies borrowed from owning services'
    committed contracts -- file-based, so the gateway never imports another service."""
    schema = get_openapi(
        title=app.title, version=app.version, description=app.description, routes=app.routes
    )
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components["ErrorResponse"] = ERROR_RESPONSE_SCHEMA
    if contracts_dir is None:
        return schema
    for backend in {r.backend for r in CATALOG if r.backend and not r.is_stub}:
        contract_file = contracts_dir / f"{backend}.json"
        if not contract_file.is_file():
            continue
        upstream = json.loads(contract_file.read_text())
        upstream_ops = {
            (_normalise(path), method.upper()): op
            for path, ops in upstream.get("paths", {}).items()
            for method, op in ops.items()
        }
        for route in CATALOG:
            if route.backend != backend or route.is_stub:
                continue
            up = upstream_ops.get((_normalise(f"/api/v1{route.path}"), route.method))
            op = schema["paths"].get(f"/api/v1{route.path}", {}).get(route.method.lower())
            if not up or op is None:
                continue
            if "requestBody" in up:
                op["requestBody"] = up["requestBody"]
            for code, response in up.get("responses", {}).items():
                if not code.startswith(("4", "5")):
                    op.setdefault("responses", {})[code] = response
            upstream_success = {c for c in up.get("responses", {}) if c.startswith("2")}
            if upstream_success and "200" not in upstream_success:
                op["responses"].pop("200", None)
        upstream_schemas = upstream.get("components", {}).get("schemas", {})
        for name in _referenced_schema_names(schema["paths"], upstream_schemas):
            components.setdefault(name, upstream_schemas[name])
    return schema


def _referenced_schema_names(node: Any, available: dict[str, Any]) -> set[str]:
    """Schemas reachable from `node` through `$ref`s -- so internal-only upstream schemas
    (e.g. a service-to-service response) never leak into the public contract."""
    found: set[str] = set()
    pending: list[Any] = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            ref = current.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[-1]
                if name in available and name not in found:
                    found.add(name)
                    pending.append(available[name])
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return found


def create_app(
    *,
    settings: Settings | None = None,
    rate_limiter: RateLimiter | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
    contracts_dir: Path | None = DEFAULT_CONTRACTS_DIR,
) -> FastAPI:
    """Build the gateway. Tests inject an HTTP transport (fake upstreams) and may
    inject a rate limiter; production passes neither."""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    app = FastAPI(
        title="api-gateway",
        version="1.0.0",
        description="Public API surface of the BuVi platform (build spec Section 9).",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.rate_limit_policy = resolved.rate_limit_policy()
    app.state.rate_limiter = rate_limiter
    app.state.http_transport = http_transport

    # At the edge a client never chooses its request id (Section 22).
    app.add_middleware(RequestIdMiddleware, trust_inbound=False)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(build_router())

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = compose_openapi(app, contracts_dir)
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app


def run() -> None:  # pragma: no cover - console entry point
    import uvicorn

    uvicorn.run(
        "api_gateway.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container bind
        port=8000,
        log_level=get_settings().log_level.lower(),
    )
