"""Structured logging with request correlation (Section 22).

Two rules this module exists to enforce:

* every log line carries `request_id` so one HTTP request is traceable across
  services (Section 22);
* nothing secret-shaped is ever written (Section 24, "Credentials and secrets").
  `redact` is the single chokepoint for that, used by the audit writer and by
  any handler that logs a payload.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any, Final

#: Correlation id for the request currently being handled.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

#: Field names whose values never reach a log line, an error response, or an
#: audit `before_state`/`after_state` diff (Section 24).
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
        "cookie",
        "set-cookie",
        "totp_secret",
        "mfa_secret",
        "provisioning_uri",
        "dsn",
        "connection_string",
        "vault_token",
    }
)

REDACTED: Final = "[REDACTED]"


def redact(value: Any) -> Any:
    """Recursively replace secret-shaped values with a placeholder.

    Matching is on the *key*, not the value, so a rotated credential is redacted
    even when its shape changes.
    """
    if isinstance(value, dict):
        return {
            key: (REDACTED if str(key).lower() in SECRET_FIELD_NAMES else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the active `request_id` attached."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        extra = getattr(record, "context", None)
        if isinstance(extra, dict):
            payload["context"] = redact(extra)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Uvicorn's own access log duplicates the correlation-carrying log below.
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False
