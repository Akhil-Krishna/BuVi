"""Structured JSON logging with request correlation and secret redaction (Sections 22, 24)."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any, Final

#: Correlation id of the request currently being handled (Section 22).
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

#: Keys whose values never reach a log line, error response or audit diff (Section 24).
SECRET_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "secret",
        "client_secret",
        "secret_hash",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "code",
        "code_verifier",
        "api_key",
        "authorization",
        "x-service-authorization",
        "cookie",
        "set-cookie",
        "totp_secret",
        "mfa_secret",
        "provisioning_uri",
        "dsn",
        "connection_string",
        "vault_token",
        "session_token",
    }
)
REDACTED: Final = "[REDACTED]"


def redact(value: Any) -> Any:
    """Recursively replace values under secret-shaped keys (matched case-insensitively)."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if str(key).lower() in SECRET_FIELD_NAMES else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """One JSON object per line, carrying the service name and active `request_id`."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            payload["context"] = redact(context)
        return json.dumps(payload, default=str)


def configure_logging(service: str, level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger.

    Uvicorn's access log is silenced: it writes request paths, and some public paths
    carry credentials (e.g. `/invitations/{token}/accept`, Section 9).
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
    access.disabled = True
