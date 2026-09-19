"""Usage batches (Phase A11): ack only once stored; retry the whole batch while the store is down;
drop only what can never parse."""

from __future__ import annotations

import uuid

import pytest

from platform_contracts import BillingUsageRecorded
from worker_runtime.application.services.run_dispatcher import (
    Disposition,
    OrchestratorUnavailableError,
)
from worker_runtime.application.services.usage_aggregator import UsageAggregator

pytestmark = pytest.mark.unit


class FakeStore:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.batches: list[list[BillingUsageRecorded]] = []

    async def store_usage(self, records: list[BillingUsageRecorded]) -> None:
        self.batches.append(records)
        if self.error is not None:
            raise self.error


def _event(metric: str = "llm_input_tokens") -> bytes:
    return (
        BillingUsageRecorded(tenant_id=uuid.uuid4(), metric=metric, quantity=10, model="m")  # type: ignore[arg-type]
        .model_dump_json()
        .encode()
    )


async def test_a_stored_batch_is_acked_in_one_call() -> None:
    store = FakeStore()
    batch = [_event(), _event("query_execution_ms")]
    decisions = await UsageAggregator(store=store, retry_seconds=5).handle(batch)
    assert [d.disposition for d in decisions] == [Disposition.ACK, Disposition.ACK]
    assert len(store.batches) == 1 and len(store.batches[0]) == 2


async def test_malformed_events_are_dropped_and_the_rest_stored() -> None:
    store = FakeStore()
    batch = [b"not json", _event(), b'{"schema_version": "2.0"}']
    decisions = await UsageAggregator(store=store, retry_seconds=5).handle(batch)
    assert [d.disposition for d in decisions] == [
        Disposition.TERMINATE,
        Disposition.ACK,
        Disposition.TERMINATE,
    ]
    assert len(store.batches[0]) == 1


async def test_an_unavailable_store_retries_every_valid_event() -> None:
    store = FakeStore(OrchestratorUnavailableError())
    decisions = await UsageAggregator(store=store, retry_seconds=7).handle([_event(), b"x"])
    assert [(d.disposition, d.delay_seconds) for d in decisions] == [
        (Disposition.RETRY, 7),
        (Disposition.TERMINATE, 0.0),
    ]


async def test_a_batch_of_only_malformed_events_never_calls_the_store() -> None:
    store = FakeStore()
    decisions = await UsageAggregator(store=store, retry_seconds=5).handle([b"{}"])
    assert [d.disposition for d in decisions] == [Disposition.TERMINATE]
    assert store.batches == []
