"""Logging for identity-service (Sections 22, 24).

JSON formatting, correlation and redaction are shared platform behaviour and live in
`platform_observability`; this module binds them to this service's name.
"""

from __future__ import annotations

from platform_observability.logging import REDACTED, redact, request_id_var
from platform_observability.logging import configure_logging as _configure_logging

__all__ = ["REDACTED", "configure_logging", "redact", "request_id_var"]


def configure_logging(level: str = "INFO") -> None:
    _configure_logging("identity-service", level)
