"""What to do with a batch of `billing.usage.recorded` messages (Sections 18.1, 23; Phase A11).

Valid events are written to analytics-orchestrator (the owner of `analytics.usage_records`) in one
call and acked only after it succeeds. The store is idempotent per `event_id`, so a batch that is
redelivered after a crash is counted once. A malformed message can never succeed and is dropped;
an unavailable orchestrator means retry the whole batch later -- usage is never dropped for that.
"""

from __future__ import annotations

import logging
from typing import Protocol

from pydantic import ValidationError

from platform_contracts import BillingUsageRecorded, SchemaVersionError
from worker_runtime.application.services.run_dispatcher import (
    Decision,
    Disposition,
    OrchestratorUnavailableError,
)

logger = logging.getLogger(__name__)


class UsageStorePort(Protocol):
    async def store_usage(self, records: list[BillingUsageRecorded]) -> None:
        """Raises `OrchestratorUnavailableError` unless the whole batch was stored."""
        ...


class UsageAggregator:
    def __init__(self, *, store: UsageStorePort, retry_seconds: float) -> None:
        self._store = store
        self._retry = retry_seconds

    async def handle(self, batch: list[bytes]) -> list[Decision]:
        """One decision per message, in order."""
        decisions: list[Decision | None] = []
        records: list[BillingUsageRecorded] = []
        for data in batch:
            try:
                records.append(BillingUsageRecorded.parse_event(data))
                decisions.append(None)
            except (ValidationError, SchemaVersionError, ValueError):
                logger.error(
                    "dropping malformed usage event", extra={"context": {"size": len(data)}}
                )
                decisions.append(Decision(Disposition.TERMINATE))
        outcome = Decision(Disposition.ACK)
        if records:
            try:
                await self._store.store_usage(records)
            except OrchestratorUnavailableError:
                logger.warning(
                    "usage store unavailable; will retry",
                    extra={"context": {"records": len(records)}},
                )
                outcome = Decision(Disposition.RETRY, self._retry)
        return [decision or outcome for decision in decisions]
