"""The Section 21 error envelope, as one shared set of FastAPI exception handlers.

Every service installs `install_error_handlers(app)`, so every 4xx/5xx on the
platform has the same shape and clients branch on `error.code`, never on
`error.message`. No stack trace, SQL, credential or upstream body is ever returned.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from platform_observability.logging import request_id_var

logger = logging.getLogger("platform_observability.errors")

HTTP_STATUS_CODES: Final[dict[int, str]] = {
    400: "BAD_REQUEST",
    401: "AUTHENTICATION_REQUIRED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    429: "RATE_LIMITED",
    501: "NOT_IMPLEMENTED",
    502: "UPSTREAM_UNAVAILABLE",
    503: "SERVICE_UNAVAILABLE",
    504: "UPSTREAM_TIMEOUT",
}


class ApiError(Exception):
    """Base for errors that map onto the envelope. Plain Python: safe in a domain layer."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        **details: Any,
    ) -> None:
        super().__init__(message or self.message)
        self.detail_message = message or self.message
        self.details: dict[str, Any] = details
        self.headers: dict[str, str] = headers or {}


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {"code": code, "message": message, "request_id": request_id_var.get()}
    }
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.detail_message,
            details=exc.details or None,
            headers=exc.headers or None,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(
            status_code=exc.status_code,
            code=HTTP_STATUS_CODES.get(exc.status_code, "HTTP_ERROR"),
            message=str(exc.detail),
            # Keep challenge headers such as the step-up WWW-Authenticate.
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Locations and types only: Pydantic's `input` can echo a password or token.
        return error_response(
            status_code=422,
            code="VALIDATION_FAILED",
            message="The request payload is invalid.",
            details={
                "fields": [
                    {"loc": [str(p) for p in err.get("loc", ())], "type": str(err.get("type", ""))}
                    for err in exc.errors()
                ]
            },
        )

    @app.exception_handler(Exception)
    async def _unexpected(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", extra={"context": {"error_type": type(exc).__name__}})
        return error_response(
            status_code=500, code="INTERNAL_ERROR", message="An unexpected error occurred."
        )
