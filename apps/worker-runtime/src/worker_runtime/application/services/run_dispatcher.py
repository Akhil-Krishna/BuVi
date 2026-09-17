"""What to do with one `analytics.run.requested` message (Sections 10.2, 18.1).

The worker never decides a run's outcome -- analytics-orchestrator does. The worker decides only
whether the message is done (ack), must be retried later (nak with delay), or can never succeed
(term). A message is acked only after the run reaches a terminal status, so a worker that dies
mid-run leaves it unacked and JetStream redelivers it.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import ValidationError

from platform_contracts import RunRequested, SchemaVersionError

logger = logging.getLogger(__name__)


class Disposition(StrEnum):
    ACK = "ack"
    RETRY = "retry"
    TERMINATE = "terminate"


@dataclass(frozen=True)
class Decision:
    disposition: Disposition
    delay_seconds: float = 0.0


class OrchestratorUnavailableError(Exception):
    """Network failure or 5xx from analytics-orchestrator."""


class RunExecutionPort(Protocol):
    async def execute(self, tenant_id: uuid.UUID, run_id: uuid.UUID) -> int:
        """HTTP status of the execute call; raises `OrchestratorUnavailableError`."""
        ...


class RunDispatcher:
    def __init__(self, *, orchestrator: RunExecutionPort, retry_base_seconds: float) -> None:
        self._orchestrator = orchestrator
        self._retry_base = retry_base_seconds

    async def handle(self, data: bytes, *, delivery_count: int) -> Decision:
        try:
            message = RunRequested.parse_event(data)
        except (ValidationError, SchemaVersionError, ValueError):
            logger.error("dropping malformed run request", extra={"context": {"size": len(data)}})
            return Decision(Disposition.TERMINATE)
        context = {"run_id": str(message.run_id), "delivery": delivery_count}
        backoff = min(self._retry_base * (2 ** max(delivery_count - 1, 0)), 120.0)
        try:
            status = await self._orchestrator.execute(message.tenant_id, message.run_id)
        except OrchestratorUnavailableError:
            logger.warning("orchestrator unavailable; will retry", extra={"context": context})
            return Decision(Disposition.RETRY, backoff)
        if status == 200:
            return Decision(Disposition.ACK)
        if status == 404:
            logger.error("run not found; dropping", extra={"context": context})
            return Decision(Disposition.TERMINATE)
        if status == 409:
            # Another execution holds the run. Check back once it should have finished.
            return Decision(Disposition.RETRY, max(backoff, 15.0))
        logger.error("run execution refused", extra={"context": {**context, "status": status}})
        return (
            Decision(Disposition.RETRY, backoff)
            if status >= 500
            else Decision(Disposition.TERMINATE)
        )
