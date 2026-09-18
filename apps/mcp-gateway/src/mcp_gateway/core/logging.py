"""Logging for mcp-gateway. Tool arguments, tool output and auth tokens are never logged
(Sections 14, 22, 24): invocations are observable through `mcp.invocations`, failures by code."""

from __future__ import annotations

import logging

from platform_observability.logging import configure_logging as _configure_logging


def configure_logging(level: str = "INFO") -> None:
    _configure_logging("mcp-gateway", level)
    for noisy in ("httpx", "httpcore", "nats", "opentelemetry"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
