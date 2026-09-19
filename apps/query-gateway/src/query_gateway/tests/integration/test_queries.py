"""Phase A4 DoD over HTTP: a valid SELECT against the sample database executes, returns a capped
result and writes a `query_executions` audit row -- and every other outcome is audited too."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI

from platform_auth import ServiceTokenIssuer
from platform_egress import EgressPolicy
from platform_secrets import InMemorySecretStore
from query_gateway.domain.value_objects.execution import (
    ConnectionCredentials,
    ExecutionError,
    ExecutionFailure,
    ExecutionLimits,
)
from query_gateway.infrastructure.connectors.postgres import PostgresQueryExecutor
from query_gateway.infrastructure.messaging.nats_usage import (
    ObservedUsageMeter,
    UnavailableUsageMeter,
)
from query_gateway.infrastructure.storage.base import InMemoryResultStore
from query_gateway.tests.conftest import (
    CUSTOMER_DB,
    Api,
    FakeServices,
    PostgresInfo,
    RecordingUsage,
    audit_rows,
    make_settings,
    reader_secret,
    running_app,
)

pytestmark = pytest.mark.integration

REVENUE_SQL = (
    "SELECT date_trunc('month', order_date) AS month, sum(amount) AS revenue "
    "FROM sales.orders WHERE status = 'completed' GROUP BY 1 ORDER BY 1"
)


async def test_valid_select_executes_capped_stores_result_and_audits(
    api: Api,
    services: FakeServices,
    results: InMemoryResultStore,
    postgres: PostgresInfo,
    platform_db: Any,
    tenant: uuid.UUID,
    usage: RecordingUsage,
) -> None:
    who = services.add_user(tenant, {"developer"})
    source = await api.data_source(tenant)

    response = await api.query(who, source, REVENUE_SQL)
    assert response.status_code == 200, response.text
    body = response.json()
    customer = await asyncpg.connect(postgres.customer_dsn())
    try:
        expected = await customer.fetch(REVENUE_SQL)
    finally:
        await customer.close()
    assert [c["name"] for c in body["columns"]] == ["month", "revenue"]
    assert [row[1] for row in body["rows"]] == [str(r["revenue"]) for r in expected]
    assert body["row_count"] == len(expected) and body["truncated"] is False
    assert body["tables"] == ["sales.orders"]
    assert body["result_handle"].startswith("memory://tenants/")

    capped = (
        await api.query(who, source, "SELECT id, amount FROM sales.orders ORDER BY id", max_rows=5)
    ).json()
    assert capped["row_count"] == 5 and capped["truncated"] is True
    assert capped["truncation_reason"] == "rows"
    assert [row[0] for row in capped["rows"]] == [1, 2, 3, 4, 5]

    stored = json.loads(results.objects[f"tenants/{tenant}/queries/{capped['query_id']}.json"])
    assert stored["rows"] == capped["rows"] and stored["truncated"] is True

    rows = await audit_rows(platform_db, tenant)
    assert [r["status"] for r in rows] == ["succeeded", "succeeded"]
    # Section 23: database time is metered per executed query (Phase A11).
    assert [(e.tenant_id, e.metric) for e in usage.events] == [
        (tenant, "query_execution_ms"),
        (tenant, "query_execution_ms"),
    ]
    assert len({e.event_id for e in usage.events}) == 2 and all(
        e.model is None for e in usage.events
    )
    last = rows[-1]
    assert str(last["id"]) == capped["query_id"]
    assert last["data_source_id"] == source
    assert last["requested_by"] == str(who.user_id)
    assert last["purpose"] == "sql_editor"
    assert last["sql_text"] == "SELECT id, amount FROM sales.orders ORDER BY id"
    assert last["row_count"] == 5 and last["bytes_returned"] > 0 and last["duration_ms"] >= 0
    assert last["result_handle"] == capped["result_handle"]
    assert json.loads(last["validation_result"])["valid"] is True


async def test_byte_cap_truncates_and_flags(
    api: Api,
    services: FakeServices,
    postgres: PostgresInfo,
    secrets: InMemorySecretStore,
    results: InMemoryResultStore,
    issuer: Any,
    tenant: uuid.UUID,
) -> None:
    from query_gateway.tests.conftest import make_settings, running_app

    async with running_app(
        make_settings(postgres, max_result_bytes=2048), services, secrets, results, issuer
    ) as (_, client):
        local = Api(client, issuer, services, secrets, postgres)
        who = services.add_user(tenant, {"developer"})
        source = await local.data_source(tenant)
        body = (
            await local.query(who, source, "SELECT id, status, amount FROM sales.orders")
        ).json()
    assert body["truncated"] is True and body["truncation_reason"] == "bytes"
    assert 0 < body["row_count"] < 2000 and body["bytes_returned"] <= 2048


async def test_agent_path_runs_for_a_chat_user_and_excludes_pii(
    api: Api, services: FakeServices, platform_db: Any, tenant: uuid.UUID
) -> None:
    client_user = services.add_user(tenant, {"client"})
    source = await api.data_source(tenant)
    orchestrator = api.service_header(subject="analytics-orchestrator")
    run_id = uuid.uuid4()

    ok = await api.query(
        client_user,
        source,
        REVENUE_SQL,
        purpose="analytics_run",
        service=orchestrator,
        run_id=str(run_id),
    )
    assert ok.status_code == 200, ok.text
    pii = await api.query(
        client_user,
        source,
        "SELECT name, email FROM sales.customers",
        purpose="analytics_run",
        service=orchestrator,
    )
    assert pii.status_code == 422
    assert pii.json()["error"]["details"]["reason"] == "PII_COLUMN"
    rows = await audit_rows(platform_db, tenant)
    assert rows[0]["run_id"] == run_id and rows[0]["purpose"] == "analytics_run"
    assert rows[1]["status"] == "rejected"


@pytest.mark.parametrize(
    ("sql", "reason", "detail"),
    [
        ("DELETE FROM sales.orders", "WRITE_OPERATION", "DELETE"),
        ("SELECT 1; DROP TABLE sales.orders", "MULTIPLE_STATEMENTS", None),
        ("SELECT pg_read_file('/etc/passwd')", "FUNCTION_NOT_ALLOWED", "pg_read_file"),
        ("SELECT * FROM pg_catalog.pg_shadow", "TABLE_NOT_ALLOWED", "pg_catalog.pg_shadow"),
        (
            "WITH d AS (DELETE FROM sales.orders RETURNING id) SELECT * FROM d",
            "WRITE_OPERATION",
            "DELETE",
        ),
    ],
)
async def test_unsafe_sql_is_rejected_audited_and_never_reaches_the_database(
    api: Api,
    services: FakeServices,
    platform_db: Any,
    postgres: PostgresInfo,
    tenant: uuid.UUID,
    sql: str,
    reason: str,
    detail: str | None,
    usage: RecordingUsage,
) -> None:
    who = services.add_user(tenant, {"org_admin"})
    source = await api.data_source(tenant)
    response = await api.query(who, source, sql)
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "QUERY_VALIDATION_FAILED"
    assert error["details"]["reason"] == reason
    assert error["details"].get("detail") == detail
    rows = await audit_rows(platform_db, tenant)
    assert len(rows) == 1
    assert rows[0]["status"] == "rejected" and rows[0]["error_code"] == "QUERY_VALIDATION_FAILED"
    assert rows[0]["sql_text"] == sql and rows[0]["result_handle"] is None
    assert str(rows[0]["id"]) == error["details"]["query_id"]
    assert usage.events == []  # a rejected query never ran, so nothing is metered
    customer = await asyncpg.connect(postgres.customer_dsn())
    try:
        assert await customer.fetchval("SELECT count(*) FROM sales.orders") == 2000
    finally:
        await customer.close()


async def test_statement_timeout_is_enforced_and_audited(
    api: Api, services: FakeServices, platform_db: Any, tenant: uuid.UUID
) -> None:
    who = services.add_user(tenant, {"developer"})
    source = await api.data_source(tenant)
    heavy = "SELECT count(*) FROM sales.orders a, sales.orders b, sales.orders c"
    response = await api.query(who, source, heavy, timeout_ms=300)
    assert response.status_code == 504, response.text
    assert response.json()["error"]["code"] == "QUERY_TIMEOUT"
    rows = await audit_rows(platform_db, tenant)
    assert rows[-1]["status"] == "timeout" and rows[-1]["error_code"] == "QUERY_TIMEOUT"


async def test_limits_above_the_configured_maximum_are_refused(
    api: Api, services: FakeServices, tenant: uuid.UUID
) -> None:
    who = services.add_user(tenant, {"developer"})
    source = await api.data_source(tenant)
    for extra in ({"max_rows": 50_001}, {"timeout_ms": 120_001}):
        response = await api.query(who, source, "SELECT id FROM sales.orders", **extra)
        assert (
            response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_FAILED"
        )


async def test_data_source_state_failures(
    api: Api,
    services: FakeServices,
    secrets: InMemorySecretStore,
    platform_db: Any,
    postgres: PostgresInfo,
    tenant: uuid.UUID,
) -> None:
    who = services.add_user(tenant, {"developer"})
    pending = services.add_data_source(tenant, status="pending")
    response = await api.query(who, pending, "SELECT id FROM sales.orders")
    assert (
        response.status_code == 409 and response.json()["error"]["code"] == "DATA_SOURCE_NOT_ACTIVE"
    )

    no_secret = services.add_data_source(tenant)
    response = await api.query(who, no_secret, "SELECT id FROM sales.orders")
    assert response.status_code == 409

    other = uuid.uuid4()
    pointed_elsewhere = services.add_data_source(
        tenant, secret_ref=f"secret/data/tenants/{other}/datasources/{uuid.uuid4()}"
    )
    response = await api.query(who, pointed_elsewhere, "SELECT id FROM sales.orders")
    assert (
        response.status_code == 502
        and response.json()["error"]["code"] == "DATA_SOURCE_UNAVAILABLE"
    )

    wrong_password = await api.data_source(tenant, password="Wrong-" + uuid.uuid4().hex)
    response = await api.query(who, wrong_password, "SELECT id FROM sales.orders")
    assert (
        response.status_code == 502
        and response.json()["error"]["code"] == "DATA_SOURCE_UNAVAILABLE"
    )

    statuses = [r["status"] for r in await audit_rows(platform_db, tenant)]
    assert statuses.count("failed") == 3


async def test_per_tenant_concurrency_cap_refuses_without_affecting_other_tenants(
    app_client: tuple[FastAPI, Any],
    api: Api,
    services: FakeServices,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
) -> None:
    app, _ = app_client
    busy = services.add_user(tenant, {"developer"})
    calm = services.add_user(other_tenant, {"developer"})
    busy_source = await api.data_source(tenant)
    calm_source = await api.data_source(other_tenant)
    limiter = app.state.limiter
    held = [limiter.slot(tenant) for _ in range(app.state.settings.tenant_max_concurrent_queries)]
    for slot in held:
        await slot.__aenter__()
    try:
        refused = await api.query(busy, busy_source, "SELECT id FROM sales.orders")
        assert (
            refused.status_code == 429
            and refused.json()["error"]["code"] == "QUERY_CONCURRENCY_LIMITED"
        )
        assert (
            await api.query(calm, calm_source, "SELECT id FROM sales.orders")
        ).status_code == 200
    finally:
        for slot in held:
            await slot.__aexit__(None, None, None)
    assert (await api.query(busy, busy_source, "SELECT id FROM sales.orders")).status_code == 200


async def test_concurrent_queries_across_the_pool_all_succeed(
    api: Api, services: FakeServices, tenant: uuid.UUID
) -> None:
    who = services.add_user(tenant, {"developer"})
    source = await api.data_source(tenant)
    responses = await asyncio.gather(*(api.query(who, source, REVENUE_SQL) for _ in range(4)))
    assert [r.status_code for r in responses] == [200] * 4


# --- The database-side guarantees, with the validator bypassed on purpose (Section 13) ----------


@pytest.fixture
async def executor(postgres: PostgresInfo) -> Any:
    instance = PostgresQueryExecutor(
        egress=EgressPolicy.from_hosts([postgres.host]),
        connect_timeout_seconds=5,
        pool_max_size=2,
        pool_idle_seconds=60,
    )
    yield instance
    await instance.close()


def _credentials(postgres: PostgresInfo, **overrides: str) -> ConnectionCredentials:
    return ConnectionCredentials.from_secret_payload(reader_secret(postgres, **overrides))


LIMITS = ExecutionLimits(max_rows=100, max_bytes=1_000_000, timeout_ms=5_000)


@pytest.mark.security
@pytest.mark.parametrize(
    ("sql", "failure"),
    [
        ("DELETE FROM sales.orders", ExecutionFailure.REJECTED_BY_DATABASE),
        ("INSERT INTO sales.scratch VALUES (1)", ExecutionFailure.REJECTED_BY_DATABASE),
        ("CREATE TABLE sales.evil (id int)", ExecutionFailure.REJECTED_BY_DATABASE),
        ("SELECT set_config('default_transaction_read_only', 'off', false)", None),
        ("SELECT 1; SELECT 2", ExecutionFailure.QUERY_FAILED),
        ("SELECT count(*) FROM orders", ExecutionFailure.QUERY_FAILED),
    ],
    ids=[
        "delete",
        "insert",
        "create",
        "set-config-does-not-escape",
        "multi-statement",
        "empty-search-path",
    ],
)
async def test_database_refuses_what_the_validator_would_have(
    executor: PostgresQueryExecutor,
    postgres: PostgresInfo,
    sql: str,
    failure: ExecutionFailure | None,
) -> None:
    source = str(uuid.uuid4())
    if failure is None:
        # Even a session setting changed inside the read-only transaction does not make the next
        # query writable: every query opens its own read-only transaction.
        await executor.execute(
            pool_key=source,
            database_name=CUSTOMER_DB,
            credentials=_credentials(postgres),
            sql=sql,
            limits=LIMITS,
        )
        with pytest.raises(ExecutionError) as info:
            await executor.execute(
                pool_key=source,
                database_name=CUSTOMER_DB,
                credentials=_credentials(postgres),
                sql="DELETE FROM sales.orders",
                limits=LIMITS,
            )
        assert info.value.failure is ExecutionFailure.REJECTED_BY_DATABASE
        return
    with pytest.raises(ExecutionError) as info:
        await executor.execute(
            pool_key=source,
            database_name=CUSTOMER_DB,
            credentials=_credentials(postgres),
            sql=sql,
            limits=LIMITS,
        )
    assert info.value.failure is failure
    assert str(info.value) == failure.value


@pytest.mark.security
async def test_executor_enforces_egress_policy(postgres: PostgresInfo) -> None:
    strict = PostgresQueryExecutor(
        egress=EgressPolicy(), connect_timeout_seconds=5, pool_max_size=1, pool_idle_seconds=60
    )
    try:
        with pytest.raises(ExecutionError) as info:
            await strict.execute(
                pool_key="x",
                database_name=CUSTOMER_DB,
                credentials=_credentials(postgres),
                sql="SELECT 1",
                limits=LIMITS,
            )
        assert info.value.failure is ExecutionFailure.DESTINATION_NOT_ALLOWED
    finally:
        await strict.close()


async def test_a_query_succeeds_while_usage_is_undeliverable_and_readiness_says_so(
    postgres: PostgresInfo,
    services: FakeServices,
    secrets: InMemorySecretStore,
    results: InMemoryResultStore,
    issuer: ServiceTokenIssuer,
    tenant: uuid.UUID,
) -> None:
    """ADR 0014: with the bus down, metering never fails a query, and the loss is observable."""
    meter = ObservedUsageMeter(UnavailableUsageMeter())
    async with running_app(
        make_settings(postgres),
        services,
        secrets,
        results,
        issuer,
        meter,  # type: ignore[arg-type]
    ) as (_, client):
        api = Api(client, issuer, services, secrets, postgres)
        who = services.add_user(tenant, {"developer"})
        source = await api.data_source(tenant)
        assert (await api.query(who, source, REVENUE_SQL)).status_code == 200
        ready = (await client.get("/health/ready")).json()
    assert meter.undelivered == 1
    assert ready["checks"]["usage"] == "degraded (1 undelivered)"
