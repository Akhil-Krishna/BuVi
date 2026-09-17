"""`POST /internal/v1/queries/validate` and delegated `on_behalf_of` (Section 13, ADR 0006)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from query_gateway.tests.conftest import Api, Caller, FakeServices, audit_rows

pytestmark = [pytest.mark.integration, pytest.mark.security]

SQL = "SELECT date_trunc('month', order_date) AS month, sum(amount) AS revenue FROM sales.orders GROUP BY 1"
VALIDATE = "/internal/v1/queries/validate"
EXECUTE = "/internal/v1/queries"


def _body(
    source: uuid.UUID,
    who: Caller | None,
    *,
    purpose: str = "analytics_run",
    run_id: uuid.UUID | None = None,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {"database_id": str(source), "sql": SQL, "purpose": purpose, **extra}
    if who is not None:
        body["on_behalf_of"] = {"tenant_id": str(who.tenant_id), "user_id": str(who.user_id)}
    if run_id is not None:
        body["run_id"] = str(run_id)
    return body


async def _post(
    api: Api, path: str, headers: dict[str, str], body: dict[str, Any]
) -> httpx.Response:
    return await api.client.post(path, json=body, headers=headers)


async def test_validate_returns_regenerated_sql_and_audits_without_executing(
    api: Api, services: FakeServices, platform_db: Any, tenant: uuid.UUID
) -> None:
    who = services.add_user(tenant, {"developer"})
    source = await api.data_source(tenant)
    headers = {**api.service_header(), "Cookie": f"buvi_session={who.token}"}
    ok = await _post(api, VALIDATE, headers, _body(source, None, purpose="sql_editor"))
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["valid"] is True and body["tables"] == ["sales.orders"]
    assert '"sales"."orders"' in body["sql"] and len(body["normalized_sql_sha256"]) == 64
    bad = await _post(
        api,
        VALIDATE,
        headers,
        {**_body(source, None, purpose="sql_editor"), "sql": "DELETE FROM sales.orders"},
    )
    assert bad.status_code == 422 and bad.json()["error"]["details"]["reason"] == "WRITE_OPERATION"
    rows = await audit_rows(platform_db, tenant)
    assert [(r["status"], r["result_handle"], r["row_count"]) for r in rows] == [
        ("validated", None, None),
        ("rejected", None, None),
    ]


async def test_orchestrator_validates_and_runs_for_a_chat_user(
    api: Api, services: FakeServices, platform_db: Any, tenant: uuid.UUID
) -> None:
    chat_user = services.add_user(tenant, {"client"})
    source = await api.data_source(tenant)
    orchestrator = api.service_header(subject="analytics-orchestrator")
    run_id = uuid.uuid4()
    validated = await _post(api, VALIDATE, orchestrator, _body(source, chat_user, run_id=run_id))
    assert validated.status_code == 200, validated.text
    executed = await _post(
        api, EXECUTE, orchestrator, _body(source, chat_user, run_id=run_id, max_rows=100)
    )
    assert executed.status_code == 200, executed.text
    rows = await audit_rows(platform_db, tenant)
    assert [r["status"] for r in rows] == ["validated", "succeeded"]
    assert all(r["run_id"] == run_id and r["requested_by"] == str(chat_user.user_id) for r in rows)


@pytest.mark.parametrize(
    ("subject", "purpose", "with_run_id"),
    [
        ("api-gateway", "analytics_run", True),
        ("analytics-orchestrator", "sql_editor", True),
        ("analytics-orchestrator", "analytics_run", False),
    ],
)
async def test_delegation_is_limited_to_orchestrator_analytics_runs(
    api: Api,
    services: FakeServices,
    tenant: uuid.UUID,
    subject: str,
    purpose: str,
    with_run_id: bool,
) -> None:
    who = services.add_user(tenant, {"org_admin"})
    source = await api.data_source(tenant)
    body = _body(source, who, purpose=purpose, run_id=uuid.uuid4() if with_run_id else None)
    for path in (VALIDATE, EXECUTE):
        response = await _post(api, path, api.service_header(subject=subject), body)
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "DELEGATION_NOT_ALLOWED"


async def test_delegated_user_is_resolved_fresh_and_tenant_bound(
    api: Api, services: FakeServices, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    orchestrator = api.service_header(subject="analytics-orchestrator")
    source = await api.data_source(tenant)
    run = uuid.uuid4()

    billing = services.add_user(tenant, {"billing_admin"})
    forbidden = await _post(api, VALIDATE, orchestrator, _body(source, billing, run_id=run))
    assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "FORBIDDEN"

    gone = services.add_user(tenant, {"client"})
    services.principals[gone.token]["inactive"] = True
    inactive = await _post(api, VALIDATE, orchestrator, _body(source, gone, run_id=run))
    assert inactive.status_code == 403 and inactive.json()["error"]["code"] == "USER_NOT_ACTIVE"

    unknown = Caller(token="x", user_id=uuid.uuid4(), tenant_id=tenant)
    assert (
        await _post(api, VALIDATE, orchestrator, _body(source, unknown, run_id=run))
    ).status_code == 401

    outsider = services.add_user(other_tenant, {"client"})
    assert (
        await _post(api, VALIDATE, orchestrator, _body(source, outsider, run_id=run))
    ).status_code == 404

    chat = services.add_user(tenant, {"client"})
    both = await _post(
        api,
        VALIDATE,
        {**orchestrator, "Cookie": f"buvi_session={chat.token}"},
        _body(source, chat, run_id=run),
    )
    assert both.status_code == 422
