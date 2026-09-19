"""Logging for notification-service. Email addresses, notification bodies, webhook secrets and
webhook response bodies are never logged: deliveries are observable through
`notification.notifications`, failures by reason code."""

from __future__ import annotations

import logging

from platform_observability.logging import configure_logging as _configure_logging


def configure_logging(level: str = "INFO") -> None:
    _configure_logging("notification-service", level)
    for noisy in ("httpx", "httpcore", "nats", "opentelemetry"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
