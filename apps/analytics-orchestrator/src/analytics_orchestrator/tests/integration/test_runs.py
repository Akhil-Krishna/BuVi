"""Phase A5 DoD over HTTP: a message produces a run; the Flow emits the Section 32 sequence; a
crash mid-run resumes from the last persisted step; the token budget fails a run with
`RUN_BUDGET_EXCEEDED`. Plus idempotency, repair loops, cancellation and delegated identity."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import socket
import uuid
from typing import Any

import pytest
import redis.asyncio as aioredis

from analytics_orchestrator.application.services.ports import ProviderResponse
from analytics_orchestrator.domain.value_objects.agent_outputs import AnalyticsRequest
from analytics_orchestrator.domain.value_objects.run_state import AnalyticsRunState
from analytics_orchestrator.infrastructure.llm.scripted_provider import ScriptedProvider
from analytics_orchestrator.tests.conftest import (
    DAILY_ROW_MARKER,
    MESSAGE,
    SECTION_32,
    FakeServices,
    Harness,
    MemoryQueue,
    PostgresInfo,
    SimulatedCrashError,
    make_settings,
    run_events,
    run_row,
    running,
)
from platform_auth import ServiceTokenIssuer

pytestmark = pytest.mark.integration


async def test_message_creates_a_queued_run_and_enqueues_it(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    run_id = await harness.start_run(who)
    assert [str(m.run_id) for m in harness.queue.messages] == [run_id]
    assert harness.queue.messages[0].tenant_id == tenant
    row = await run_row(platform_db, run_id)
    assert row["status"] == "queued" and row["requested_by"] == who.user_id
    assert row["flow_state"]["message"] == MESSAGE and row["flow_state"]["completed_steps"] == []
    stored = await platform_db.fetchval(
        "SELECT content FROM analytics.messages WHERE run_id = $1 AND role = 'user'",
        uuid.UUID(run_id),
    )
    assert stored == MESSAGE


async def test_idempotency_key_replays_the_same_run_and_rejects_reuse(
    harness: Harness, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    conversation = await harness.conversation(who)
    url = f"/api/v1/conversations/{conversation}/messages"
    headers = {**who.headers, "Idempotency-Key": "retry-7f3a"}
    first = await harness.client.post(url, json={"content": MESSAGE}, headers=headers)
    second = await harness.client.post(url, json={"content": MESSAGE}, headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json() and second.headers["idempotent-replayed"] == "true"
    assert len(harness.queue.messages) == 1
    reused = await harness.client.post(url, json={"content": "something else"}, headers=headers)
    assert reused.status_code == 409 and reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    bad = await harness.client.post(
        url, json={"content": MESSAGE}, headers={**who.headers, "Idempotency-Key": "has space"}
    )
    assert bad.status_code == 422


async def test_full_run_emits_the_section_32_sequence_and_an_artifact(
    harness: Harness, platform_db: Any, redis_url: str, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    run_id = await harness.start_run(who)

    subscriber = aioredis.Redis.from_url(redis_url)
    pubsub = subscriber.pubsub()
    await pubsub.subscribe(f"analytics:run:{run_id}")
    response = await harness.execute(who, run_id)
    assert response.status_code == 200, response.text
    assert response.json() == {"run_id": run_id, "status": "completed", "error_code": None}

    live: list[dict[str, Any]] = []
    deadline = asyncio.get_running_loop().time() + 10
    while len(live) < len(SECTION_32):
        assert asyncio.get_running_loop().time() < deadline, f"only {len(live)} live events"
        # Returns None for the (ignored) subscribe confirmation as well as on timeout.
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
        if message is not None:
            live.append(json.loads(message["data"]))
    await pubsub.aclose()
    await subscriber.aclose()

    assert await run_events(platform_db, run_id) == SECTION_32
    assert [f"{e['stage']}.{e['status']}" for e in live] == SECTION_32
    assert [e["seq"] for e in live] == list(range(1, 16))
    assert all(e["message"] and "sql" not in e["message"].lower() for e in live)

    row = await run_row(platform_db, run_id)
    state = AnalyticsRunState.model_validate(row["flow_state"])
    assert (
        row["status"] == "completed"
        and row["completed_at"]
        and row["current_stage"] == "publish_events"
    )
    assert state.artifact is not None and live[13]["artifactId"] == state.artifact.artifact_id
    assert state.artifact.chart_spec["type"] == "line"
    assert state.artifact.chart_spec["encoding"]["x"] == {"field": "month", "type": "temporal"}
    assert state.artifact.chart_spec["encoding"]["y"] == {
        "field": "revenue",
        "type": "quantitative",
    }
    assert (
        "DATE '2026-04-01'" in state.artifact.validated_sql
        and "sales.orders" in state.artifact.validated_sql
    )
    assert state.usage.calls == 4 and state.usage.total > 0

    validate, execute = harness.services.query_calls
    for call in (validate, execute):
        assert call["body"]["purpose"] == "analytics_run" and call["body"]["run_id"] == run_id
        assert call["body"]["on_behalf_of"] == {
            "tenant_id": str(tenant),
            "user_id": str(who.user_id),
        }
        assert (
            call["headers"]["x-service-authorization"]
            == "Bearer svc:query-gateway:query-gateway:execute"
        )
    assert {u.metric for u in harness.queue.usage} == {"llm_input_tokens", "llm_output_tokens"}

    replay = await harness.client.get(
        f"/internal/v1/runs/{run_id}/events",
        params={"tenant_id": str(tenant), "after_seq": 12},
        headers=harness.service_headers("api-gateway", "analytics-orchestrator:events"),
    )
    assert [e["seq"] for e in replay.json()["events"]] == [13, 14, 15]
    assistant = await platform_db.fetchval(
        "SELECT content FROM analytics.messages WHERE run_id = $1 AND role = 'assistant'",
        uuid.UUID(run_id),
    )
    assert assistant.startswith(MESSAGE[:40])


async def test_prompts_carry_data_blocks_never_rows_or_credentials(
    harness: Harness, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    run_id = await harness.start_run(who)
    assert (await harness.execute(who, run_id)).json()["status"] == "completed"
    prompts = "\n".join(harness.provider.users)
    assert "<user_request>" in prompts and "<catalog>" in prompts and "<result_schema>" in prompts
    for forbidden in (DAILY_ROW_MARKER, "secret_ref", "secret/data", "s3://", "password"):
        assert forbidden not in prompts


async def test_crash_mid_run_resumes_from_the_last_persisted_step(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    """DoD: a restart resumes instead of restarting -- no step repeated, no event re-emitted."""
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    run_id = await harness.start_run(who)

    async def crash_after_execution(step: str, _state: AnalyticsRunState) -> None:
        if step == "execute_query":
            raise SimulatedCrashError()

    harness.hook["after_step"] = crash_after_execution
    with pytest.raises(SimulatedCrashError):
        await harness.app.state.executor.execute(tenant, uuid.UUID(run_id))

    crashed = await run_row(platform_db, run_id)
    assert crashed["status"] == "running"
    assert crashed["flow_state"]["completed_steps"][-1] == "execute_query"
    assert await run_events(platform_db, run_id) == SECTION_32[:10]
    calls_before = list(harness.provider.calls)
    assert calls_before == ["AnalyticsRequest", "QueryPlan", "GeneratedSql"]

    del harness.hook["after_step"]
    resumed = await harness.execute(who, run_id)
    assert resumed.json()["status"] == "completed"
    assert harness.provider.calls == [*calls_before, "ChartSpec"]
    assert [c["path"] for c in harness.services.query_calls] == [
        "/internal/v1/queries/validate",
        "/internal/v1/queries",
    ]
    assert await run_events(platform_db, run_id) == SECTION_32


async def test_token_accounting_survives_a_crash_between_charge_and_step_persist(
    harness: Harness, platform_db: Any, redis_url: str, tenant: uuid.UUID
) -> None:
    """A model call charged just before a crash stays on the run's budget; resume adds, never resets."""
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    run_id = await harness.start_run(who)
    record = harness.queue.record

    async def crash_on_first_plan_charge(event: Any) -> None:
        await record(event)
        if event.stage == "sql" and event.metric == "llm_output_tokens":
            raise SimulatedCrashError()

    harness.queue.record = crash_on_first_plan_charge  # type: ignore[method-assign]
    with pytest.raises(SimulatedCrashError):
        await harness.app.state.executor.execute(tenant, uuid.UUID(run_id))
    harness.queue.record = record  # type: ignore[method-assign]

    crashed = (await run_row(platform_db, run_id))["flow_state"]
    assert "build_query_plan" not in crashed["completed_steps"]
    assert crashed["usage"]["calls"] == 2  # intent + the plan call charged before the crash
    deadline = crashed["deadline"]

    resumed = await harness.execute(who, run_id)
    assert resumed.json()["status"] == "completed"
    final = (await run_row(platform_db, run_id))["flow_state"]
    assert final["usage"]["calls"] == 5  # the interrupted plan call is paid again, not forgotten
    assert final["deadline"] == deadline  # the run timeout does not restart either
    billed = sum(e.quantity for e in harness.queue.usage if e.run_id == uuid.UUID(run_id))
    assert final["usage"]["input_tokens"] + final["usage"]["output_tokens"] == billed
    ledger = aioredis.Redis.from_url(redis_url)
    key = f"llm:tokens:{tenant}:{dt.datetime.now(dt.UTC):%Y%m%d}"
    assert int(await ledger.get(key)) == billed
    await ledger.aclose()


async def test_run_budget_exceeded_before_any_call(
    postgres: PostgresInfo,
    redis_url: str,
    services: FakeServices,
    provider: ScriptedProvider,
    queue: MemoryQueue,
    issuer: ServiceTokenIssuer,
    platform_db: Any,
    tenant: uuid.UUID,
) -> None:
    settings = make_settings(
        postgres, redis_url, run_token_budget=1_000, llm_max_tokens_per_call=4_096
    )
    async with running(settings, services, provider, queue, issuer) as h:
        who = services.add_user(tenant, {"client"})
        services.add_data_source(tenant)
        run_id = await h.start_run(who)
        result = (await h.execute(who, run_id)).json()
    assert result == {"run_id": run_id, "status": "failed", "error_code": "RUN_BUDGET_EXCEEDED"}
    assert provider.calls == []  # enforced before the call, not after
    assert await run_events(platform_db, run_id) == [
        "intent.started",
        "intent.failed",
        "run.failed",
    ]


async def test_run_budget_exceeded_by_actual_usage_fails_the_run(
    postgres: PostgresInfo,
    redis_url: str,
    services: FakeServices,
    provider: ScriptedProvider,
    queue: MemoryQueue,
    issuer: ServiceTokenIssuer,
    platform_db: Any,
    tenant: uuid.UUID,
) -> None:
    settings = make_settings(
        postgres, redis_url, run_token_budget=5_000, llm_max_tokens_per_call=256
    )
    request = AnalyticsRequest(intent="visualization", title="Monthly revenue", metrics=["revenue"])
    provider.queue(
        AnalyticsRequest,
        ProviderResponse(output=request, input_tokens=3_000, output_tokens=3_000, model="scripted"),
    )
    async with running(settings, services, provider, queue, issuer) as h:
        who = services.add_user(tenant, {"client"})
        services.add_data_source(tenant)
        run_id = await h.start_run(who)
        result = (await h.execute(who, run_id)).json()
    assert result["status"] == "failed" and result["error_code"] == "RUN_BUDGET_EXCEEDED"
    assert await run_events(platform_db, run_id) == [
        "intent.started",
        "intent.failed",
        "run.failed",
    ]
    row = await run_row(platform_db, run_id)
    assert row["flow_state"]["usage"]["input_tokens"] == 3_000
    message = await platform_db.fetchval(
        "SELECT message FROM analytics.run_events WHERE run_id = $1 AND stage = 'run'",
        uuid.UUID(run_id),
    )
    assert message == "This request exceeded its processing budget."


async def test_tenant_daily_budget_is_enforced(
    postgres: PostgresInfo,
    redis_url: str,
    services: FakeServices,
    provider: ScriptedProvider,
    queue: MemoryQueue,
    issuer: ServiceTokenIssuer,
    tenant: uuid.UUID,
) -> None:
    ledger = aioredis.Redis.from_url(redis_url)
    await ledger.set(f"llm:tokens:{tenant}:{dt.datetime.now(dt.UTC):%Y%m%d}", 1_999_000)
    await ledger.aclose()
    async with running(make_settings(postgres, redis_url), services, provider, queue, issuer) as h:
        who = services.add_user(tenant, {"client"})
        services.add_data_source(tenant)
        result = (await h.execute(who, await h.start_run(who))).json()
    assert result["error_code"] == "TENANT_BUDGET_EXCEEDED" and provider.calls == []


async def test_rejected_sql_is_repaired_at_most_twice(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    harness.services.validate_rejections = [
        {"reason": "FUNCTION_NOT_ALLOWED", "detail": "pg_sleep"}
    ]
    ok = (await harness.execute(who, await harness.start_run(who))).json()
    assert ok["status"] == "completed"
    assert harness.provider.calls.count("GeneratedSql") == 2
    assert "FUNCTION_NOT_ALLOWED: pg_sleep" in harness.provider.users[3]

    harness.services.validate_rejections = [{"reason": "WRITE_OPERATION", "detail": "DELETE"}] * 3
    run_id = await harness.start_run(who)
    failed = (await harness.execute(who, run_id)).json()
    assert failed["error_code"] == "QUERY_REJECTED"
    assert (await run_events(platform_db, run_id))[-2:] == ["validation.failed", "run.failed"]


async def test_cancellation_of_queued_and_running_runs(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    queued = await harness.start_run(who)
    cancelled = await harness.client.post(f"/api/v1/runs/{queued}/cancel", headers=who.headers)
    assert cancelled.status_code == 202 and cancelled.json()["status"] == "cancelled"
    assert await run_events(platform_db, queued) == ["run.failed"]
    assert (await harness.execute(who, queued)).json()["status"] == "cancelled"
    again = await harness.client.post(f"/api/v1/runs/{queued}/cancel", headers=who.headers)
    assert again.status_code == 409

    running_run = await harness.start_run(who)

    async def cancel_after_intent(step: str, _state: AnalyticsRunState) -> None:
        if step == "classify_intent":
            response = await harness.client.post(
                f"/api/v1/runs/{running_run}/cancel", headers=who.headers
            )
            assert response.status_code == 202

    harness.hook["after_step"] = cancel_after_intent
    result = (await harness.execute(who, running_run)).json()
    assert result == {"run_id": running_run, "status": "cancelled", "error_code": "CANCELLED"}
    assert await run_events(platform_db, running_run) == [
        "intent.started",
        "intent.completed",
        "run.failed",
    ]
    assert "RetrieveSchema" not in harness.provider.calls and harness.services.query_calls == []


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ("inactive", "NOT_AUTHORIZED"),
        ("no_source", "NO_DATA_SOURCE"),
        ("two_sources", "DATA_SOURCE_SELECTION_REQUIRED"),
        ("unsupported", "REQUEST_NOT_SUPPORTED"),
        ("metadata_down", "UPSTREAM_UNAVAILABLE"),
    ],
)
async def test_run_failures_are_typed_and_user_safe(
    harness: Harness, platform_db: Any, tenant: uuid.UUID, setup: str, code: str
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    message = MESSAGE
    if setup != "no_source":
        harness.services.add_data_source(tenant)
    if setup == "two_sources":
        harness.services.add_data_source(tenant)
    if setup == "inactive":
        harness.services.inactive_users.add(str(who.user_id))
    if setup == "unsupported":
        message = "write me a poem about cats"
    if setup == "metadata_down":
        harness.services.metadata_down = True
    run_id = await harness.start_run(who, message)
    result = (await harness.execute(who, run_id)).json()
    assert result["status"] == "failed" and result["error_code"] == code
    assert (await run_events(platform_db, run_id))[-1] == "run.failed"


async def test_explicit_data_source_must_be_active_for_the_tenant(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    foreign = harness.services.add_data_source(other_tenant)
    run_id = await harness.start_run(who, data_source_id=str(foreign))
    assert (await harness.execute(who, run_id)).json()["error_code"] == "DATA_SOURCE_NOT_ACTIVE"


async def test_a_second_execution_of_a_running_run_is_refused(
    harness: Harness, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    run_id = await harness.start_run(who)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold(step: str, _state: AnalyticsRunState) -> None:
        if step == "load_context":
            entered.set()
            await release.wait()

    harness.hook["after_step"] = hold
    first = asyncio.create_task(harness.app.state.executor.execute(tenant, uuid.UUID(run_id)))
    await entered.wait()
    busy = await harness.execute(who, run_id)
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "RUN_BUSY"
    release.set()
    assert (await first).status == "completed"


async def test_enqueue_failure_fails_the_run_visibly(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    conversation = await harness.conversation(who)
    harness.queue.fail = True
    response = await harness.client.post(
        f"/api/v1/conversations/{conversation}/messages",
        json={"content": MESSAGE},
        headers=who.headers,
    )
    assert response.status_code == 503 and response.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    row = await platform_db.fetchrow(
        "SELECT id, status, error_code FROM analytics.runs WHERE conversation_id = $1",
        uuid.UUID(conversation),
    )
    assert (row["status"], row["error_code"]) == ("failed", "RUN_ENQUEUE_FAILED")


@pytest.mark.security
async def test_a_full_run_opens_no_outbound_connection_and_no_chroma_client(
    harness: Harness, tenant: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0006: CrewAI telemetry is off and chromadb, though imported, is never used."""
    import chromadb

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a Chroma client was created")

    for name in ("Client", "HttpClient", "PersistentClient", "EphemeralClient", "CloudClient"):
        if hasattr(chromadb, name):
            monkeypatch.setattr(chromadb, name, forbidden)
    original_connect = socket.socket.connect
    attempted: list[object] = []

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if (
            isinstance(host, str)
            and host not in ("127.0.0.1", "::1", "localhost")
            and not host.startswith("/")
        ):
            attempted.append(host)
            raise OSError("outbound connection blocked by test")
        return original_connect(self, address)

    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    run_id = await harness.start_run(who)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    result = (await harness.execute(who, run_id)).json()
    monkeypatch.undo()
    assert result["status"] == "completed", result
    assert attempted == []
