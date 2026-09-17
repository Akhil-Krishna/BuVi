"""`POST /internal/v1/results/read` (Section 13, Phase A6): stored rows for artifact data."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from query_gateway.infrastructure.storage.base import InMemoryResultStore
from query_gateway.tests.conftest import Api, FakeServices

pytestmark = [pytest.mark.integration, pytest.mark.security]

SQL = "SELECT date_trunc('month', order_date) AS month, sum(amount) AS revenue FROM sales.orders GROUP BY 1 ORDER BY 1"
READ = "/internal/v1/results/read"


async def _analytics_result(api: Api, services: FakeServices, tenant: uuid.UUID) -> dict[str, Any]:
    chat_user = services.add_user(tenant, {"client"})
    source = await api.data_source(tenant)
    response = await api.client.post(
        "/internal/v1/queries",
        json={
            "database_id": str(source),
            "sql": SQL,
            "purpose": "analytics_run",
            "run_id": str(uuid.uuid4()),
            "on_behalf_of": {"tenant_id": str(tenant), "user_id": str(chat_user.user_id)},
        },
        headers=api.service_header(subject="analytics-orchestrator"),
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _reader(
    api: Api, subject: str = "dashboard-service", scope: str = "query-gateway:results"
) -> dict[str, str]:
    return api.service_header(subject=subject, scope=scope)


async def test_dashboard_service_reads_the_stored_rows(
    api: Api, services: FakeServices, tenant: uuid.UUID
) -> None:
    executed = await _analytics_result(api, services, tenant)
    response = await api.client.post(
        READ,
        json={"tenant_id": str(tenant), "result_handle": executed["result_handle"]},
        headers=_reader(api),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["query_id"] == executed["query_id"]
    assert body["rows"] == executed["rows"] and body["row_count"] == len(executed["rows"])
    assert [c["name"] for c in body["columns"]] == ["month", "revenue"]


async def test_other_tenant_forged_handle_and_sql_editor_results_are_404(
    api: Api, services: FakeServices, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    executed = await _analytics_result(api, services, tenant)
    handle = executed["result_handle"]
    for tenant_id, forged in (
        (other_tenant, handle),
        (other_tenant, handle.replace(str(tenant), str(other_tenant))),
        (tenant, handle.replace(executed["query_id"], str(uuid.uuid4()))),
        (tenant, handle + "?x=1"),
        (tenant, "s3://query-results/../../etc/passwd"),
    ):
        response = await api.client.post(
            READ, json={"tenant_id": str(tenant_id), "result_handle": forged}, headers=_reader(api)
        )
        assert response.status_code == 404, (forged, response.text)

    developer = services.add_user(tenant, {"developer"})
    source = await api.data_source(tenant)
    editor = await api.query(developer, source, SQL)
    assert editor.status_code == 200, editor.text
    response = await api.client.post(
        READ,
        json={"tenant_id": str(tenant), "result_handle": editor.json()["result_handle"]},
        headers=_reader(api),
    )
    assert response.status_code == 404


async def test_only_allowed_readers_with_the_scope(
    api: Api, services: FakeServices, tenant: uuid.UUID
) -> None:
    executed = await _analytics_result(api, services, tenant)
    body = {"tenant_id": str(tenant), "result_handle": executed["result_handle"]}
    assert (await api.client.post(READ, json=body)).status_code == 401
    wrong_scope = await api.client.post(
        READ, json=body, headers=_reader(api, scope="query-gateway:execute")
    )
    assert wrong_scope.status_code == 403
    wrong_caller = await api.client.post(
        READ, json=body, headers=_reader(api, subject="api-gateway")
    )
    assert wrong_caller.status_code == 403
    assert wrong_caller.json()["error"]["code"] == "RESULT_READER_NOT_ALLOWED"


async def test_expired_or_swept_results_are_410(
    api: Api,
    services: FakeServices,
    tenant: uuid.UUID,
    results: InMemoryResultStore,
    platform_db: Any,
) -> None:
    executed = await _analytics_result(api, services, tenant)
    body = {"tenant_id": str(tenant), "result_handle": executed["result_handle"]}
    results.objects.clear()
    swept = await api.client.post(READ, json=body, headers=_reader(api))
    assert swept.status_code == 410 and swept.json()["error"]["code"] == "RESULT_EXPIRED"

    executed = await _analytics_result(api, services, tenant)
    await platform_db.execute("ALTER TABLE query_gateway.query_executions DISABLE TRIGGER USER")
    await platform_db.execute(
        "UPDATE query_gateway.query_executions SET created_at = $1 WHERE id = $2",
        dt.datetime.now(dt.UTC) - dt.timedelta(days=2),
        uuid.UUID(executed["query_id"]),
    )
    await platform_db.execute("ALTER TABLE query_gateway.query_executions ENABLE TRIGGER USER")
    expired = await api.client.post(
        READ,
        json={"tenant_id": str(tenant), "result_handle": executed["result_handle"]},
        headers=_reader(api),
    )
    assert expired.status_code == 410
