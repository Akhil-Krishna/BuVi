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
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from identity_service.api.v1.health import router as health_router
from identity_service.api.v1.router import api_router
from identity_service.core.config import Settings, get_settings
from identity_service.core.logging import configure_logging, request_id_var
from identity_service.core.telemetry import new_request_id
from identity_service.dependencies import resolve_principal
from identity_service.domain.errors import DomainError
from identity_service.infrastructure.db.session import create_engine, create_session_factory
from identity_service.infrastructure.email.sender import EmailSender, SmtpEmailSender
from identity_service.infrastructure.oidc.client import KeycloakOidcClient, OidcClient
from identity_service.infrastructure.secrets.store import (
    InMemorySecretStore,
    SecretStore,
    VaultSecretStore,
)
from platform_auth import install_principal_resolver

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


def _error_response(
    *, status_code: int, code: str, message: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    """The Section 21 envelope. Every 4xx/5xx in this service goes through it."""
    body: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id_var.get(),
        }
    }
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=body)


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
            else VaultSecretStore(settings, http)
        )
    if getattr(app.state, "oidc", None) is None:
        app.state.oidc = KeycloakOidcClient(settings, http)
    if getattr(app.state, "email", None) is None:
        app.state.email = SmtpEmailSender(settings)

    try:
        yield
    finally:
        await http.aclose()
        await engine.dispose()


def create_app(
    *,
    settings: Settings | None = None,
    secrets: SecretStore | None = None,
    oidc: OidcClient | None = None,
    email: EmailSender | None = None,
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
    )
    app.state.settings = resolved
    app.state.secrets = secrets
    app.state.oidc = oidc
    app.state.email = email

    install_principal_resolver(app, resolve_principal)

    @app.middleware("http")
    async def correlate(request: Request, call_next: Any) -> Response:
        """Attach a `request_id` to the request, the logs and the response.

        An inbound header is honoured so a trace started at api-gateway carries
        through (Section 22); one is minted when absent.
        """
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        token = request_id_var.set(request_id)
        try:
            response: Response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(DomainError)
    async def handle_domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        return _error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.detail_message,
            details=exc.details or None,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {
            401: "AUTHENTICATION_REQUIRED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            429: "RATE_LIMITED",
        }
        response = _error_response(
            status_code=exc.status_code,
            code=codes.get(exc.status_code, "HTTP_ERROR"),
            message=str(exc.detail),
        )
        # Preserve challenge headers such as the step-up WWW-Authenticate.
        for key, value in (exc.headers or {}).items():
            response.headers[key] = value
        return response

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            status_code=422,
            code="VALIDATION_FAILED",
            message="The request payload is invalid.",
            # Field names and locations only. Pydantic's `input` echoes the
            # submitted value, which can be a password or a token (Section 21).
            details={
                "fields": [
                    {
                        "loc": [str(part) for part in error.get("loc", ())],
                        "type": str(error.get("type", "")),
                    }
                    for error in exc.errors()
                ]
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        # Full diagnostic to the log, keyed by request_id; nothing to the client
        # (Section 21: never return a stack trace or driver error text).
        logger.exception("Unhandled error", extra={"context": {"error_type": type(exc).__name__}})
        return _error_response(
            status_code=500,
            code="INTERNAL_ERROR",
            message="An unexpected error occurred.",
        )

    app.include_router(health_router)
    app.include_router(api_router)
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
