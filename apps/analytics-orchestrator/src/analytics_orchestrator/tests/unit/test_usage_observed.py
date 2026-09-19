"""LLM usage the bus refused is never silent (ADR 0014): logged in full for replay, counted."""

from __future__ import annotations

import logging
import uuid

import pytest

from analytics_orchestrator.infrastructure.messaging.nats_queue import ObservedUsageSink
from platform_contracts import BillingUsageRecorded

pytestmark = pytest.mark.unit


class Down:
    async def record(self, event: BillingUsageRecorded) -> None:
        raise ConnectionError("nats down")


async def test_an_undelivered_token_event_is_logged_and_counted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sink = ObservedUsageSink(Down())
    event = BillingUsageRecorded(
        tenant_id=uuid.uuid4(), metric="llm_input_tokens", quantity=900, model="m", stage="sql"
    )
    with caplog.at_level(logging.ERROR):
        await sink.record(event)
    assert sink.undelivered == 1
    [record] = [r for r in caplog.records if r.getMessage() == "usage event undelivered"]
    assert record.context["event"]["event_id"] == str(event.event_id)  # type: ignore[attr-defined]
    assert record.context["event"]["quantity"] == 900  # type: ignore[attr-defined]
