"""A usage event the bus refused is never silent (ADR 0014): logged in full for replay, counted."""

from __future__ import annotations

import json
import logging
import uuid

import pytest

from platform_contracts import BillingUsageRecorded
from query_gateway.infrastructure.messaging.nats_usage import (
    ObservedUsageMeter,
    UnavailableUsageMeter,
)

pytestmark = pytest.mark.unit


async def test_an_undelivered_event_is_logged_whole_and_counted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    meter = ObservedUsageMeter(UnavailableUsageMeter())
    event = BillingUsageRecorded(tenant_id=uuid.uuid4(), metric="query_execution_ms", quantity=1234)
    with caplog.at_level(logging.ERROR):
        await meter.record(event)  # never raises: a query must not fail for metering
    assert meter.undelivered == 1
    [record] = [r for r in caplog.records if r.getMessage() == "usage event undelivered"]
    logged = record.context["event"]  # type: ignore[attr-defined]
    assert BillingUsageRecorded.parse_event(json.dumps(logged)) == event


async def test_a_delivered_event_is_not_counted() -> None:
    class Sink:
        def __init__(self) -> None:
            self.events: list[BillingUsageRecorded] = []

        async def record(self, event: BillingUsageRecorded) -> None:
            self.events.append(event)

    inner = Sink()
    meter = ObservedUsageMeter(inner)
    await meter.record(
        BillingUsageRecorded(tenant_id=uuid.uuid4(), metric="query_execution_ms", quantity=1)
    )
    assert meter.undelivered == 0 and len(inner.events) == 1
