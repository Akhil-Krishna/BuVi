"""Logging for api-gateway; shared behaviour lives in `platform_observability`."""

from __future__ import annotations

from platform_observability.logging import configure_logging as _configure_logging


def configure_logging(level: str = "INFO") -> None:
    _configure_logging("api-gateway", level)
