"""Logging for visualization-service."""

from __future__ import annotations

from platform_observability.logging import configure_logging as _configure_logging


def configure_logging(level: str = "INFO") -> None:
    _configure_logging("visualization-service", level)
