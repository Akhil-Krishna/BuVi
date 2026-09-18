"""Logging for semantic-service. Model outputs, prompts and user messages are never logged
(Sections 11, 22, 24): run progress is observable through `run_events`, failures by code."""

from __future__ import annotations

import logging

from platform_observability.logging import configure_logging as _configure_logging


def configure_logging(level: str = "INFO") -> None:
    _configure_logging("semantic-service", level)
    for noisy in ("httpx", "anthropic", "crewai", "LiteLLM", "chromadb", "opentelemetry"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
