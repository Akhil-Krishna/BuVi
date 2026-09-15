"""DoD: "no credential ever appears in a response, log, or error message" -- across success and
every failure path, plus the audit table and the stored result."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from query_gateway.infrastructure.storage.base import InMemoryResultStore
from query_gateway.tests.conftest import (
    READER_PASSWORD,
    READER_USER,
    Api,
    FakeServices,
    PostgresInfo,
)

pytestmark = [pytest.mark.integration, pytest.mark.security]


async def test_credentials_never_leave_the_secret_store(
    api: Api,
    services: FakeServices,
    postgres: PostgresInfo,
    results: InMemoryResultStore,
    platform_db: Any,
    tenant: uuid.UUID,
    captured_logs: list[str],
) -> None:
    wrong = "Wrong-" + uuid.uuid4().hex
    who = services.add_user(tenant, {"developer"})
    good = await api.data_source(tenant)
    bad_password = await api.data_source(tenant, password=wrong)
    bad_port = await api.data_source(tenant, port="1")
    responses = [
        await api.query(who, good, "SELECT id, amount FROM sales.orders ORDER BY id", max_rows=10),
        await api.query(who, good, "DELETE FROM sales.orders"),
        await api.query(
            who,
            good,
            "SELECT count(*) FROM sales.orders a, sales.orders b, sales.orders c",
            timeout_ms=200,
        ),
        await api.query(who, good, "SELECT nope FROM sales.orders"),
        await api.query(who, bad_password, "SELECT id FROM sales.orders"),
        await api.query(who, bad_port, "SELECT id FROM sales.orders"),
    ]
    assert [r.status_code for r in responses] == [200, 422, 504, 422, 502, 502]
    sensitive = [READER_PASSWORD, wrong, READER_USER, f"{postgres.host}:{postgres.port}"]
    for response in responses:
        text = response.text + json.dumps(dict(response.headers))
        for value in sensitive:
            assert value not in text, value
    assert captured_logs
    for line in captured_logs:
        for value in sensitive:
            assert value not in line, (value, line[:200])
    rows = [
        r["row"]
        for r in await platform_db.fetch(
            "SELECT row_to_json(q)::text AS row FROM query_gateway.query_executions q WHERE tenant_id = $1",
            tenant,
        )
    ]
    assert len(rows) == 6
    for row in rows:
        for value in sensitive:
            assert value not in row
    for payload in results.objects.values():
        for value in sensitive:
            assert value.encode() not in payload
