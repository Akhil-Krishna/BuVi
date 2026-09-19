"""Message dispositions: ack only on a finished run; retry on transient failure; drop the unrecoverable."""

from __future__ import annotations

import json
import uuid

import pytest

from worker_runtime.application.services.run_dispatcher import (
    Disposition,
    OrchestratorUnavailableError,
    RunDispatcher,
)

pytestmark = pytest.mark.unit


class FakeOrchestrator:
    def __init__(self, result: int | Exception) -> None:
        self.result = result
        self.calls: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def execute(self, tenant_id: uuid.UUID, run_id: uuid.UUID) -> int:
        self.calls.append((tenant_id, run_id))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _message(**overrides: object) -> bytes:
    body = {
        "schema_version": "1.0",
        "run_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "conversation_id": str(uuid.uuid4()),
    }
    body.update(overrides)
    return json.dumps(body).encode()


@pytest.mark.parametrize(
    ("result", "disposition"),
    [
        (200, Disposition.ACK),
        (404, Disposition.TERMINATE),
        (409, Disposition.RETRY),
        (401, Disposition.TERMINATE),
        (OrchestratorUnavailableError(), Disposition.RETRY),
    ],
)
async def test_dispositions(result: int | Exception, disposition: Disposition) -> None:
    orchestrator = FakeOrchestrator(result)
    decision = await RunDispatcher(orchestrator=orchestrator, retry_base_seconds=1).handle(
        _message(), delivery_count=1
    )
    assert decision.disposition is disposition
    assert len(orchestrator.calls) == 1


@pytest.mark.parametrize(
    "payload",
    [b"not json", _message(schema_version="2.0"), _message(run_id="nope"), _message(extra=1)],
)
async def test_malformed_or_unknown_major_version_is_dropped_without_calling(
    payload: bytes,
) -> None:
    orchestrator = FakeOrchestrator(200)
    decision = await RunDispatcher(orchestrator=orchestrator, retry_base_seconds=1).handle(
        payload, delivery_count=1
    )
    assert decision.disposition is Disposition.TERMINATE and orchestrator.calls == []


async def test_retry_backoff_grows_and_is_capped() -> None:
    dispatcher = RunDispatcher(
        orchestrator=FakeOrchestrator(OrchestratorUnavailableError()), retry_base_seconds=5
    )
    delays = [
        (await dispatcher.handle(_message(), delivery_count=n)).delay_seconds for n in (1, 2, 3, 10)
    ]
    assert delays == [5, 10, 20, 120]


async def test_a_retry_on_the_last_delivery_is_reported_as_abandoned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """JetStream never redelivers past max_deliver, so "will retry" there would be a lie: the run
    would sit queued with no signal. The last retry is terminated and logged at ERROR."""
    dispatcher = RunDispatcher(
        orchestrator=FakeOrchestrator(OrchestratorUnavailableError()),
        retry_base_seconds=1,
        max_deliveries=5,
    )
    earlier = await dispatcher.handle(_message(), delivery_count=4)
    assert earlier.disposition is Disposition.RETRY
    with caplog.at_level("ERROR"):
        last = await dispatcher.handle(_message(), delivery_count=5)
    assert last.disposition is Disposition.TERMINATE
    assert any(r.getMessage() == "run request abandoned: retries exhausted" for r in caplog.records)
