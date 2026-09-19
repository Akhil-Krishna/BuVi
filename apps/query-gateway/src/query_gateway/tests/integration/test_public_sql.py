"""The public SQL API (Section 9 `/sql/*`; Phase A10) over HTTP: per-connection grants, the
export step-up, history, and the gateway-only boundary."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from query_gateway.tests.conftest import Api, Caller, FakeServices, audit_rows

pytestmark = [pytest.mark.integration, pytest.mark.security]

SQL = "SELECT status, count(*) AS n FROM sales.orders GROUP BY status ORDER BY status"


def _gateway(api: Api) -> dict[str, str]:
    return api.service_header(scope="query-gateway:proxy")


async def _post(
    api: Api, who: Caller, path: str, source: uuid.UUID, sql: str = SQL, **extra: Any
) -> Any:
    headers = {**_gateway(api), "Cookie": f"buvi_session={who.token}"}
    body = {"database_id": str(source), "sql": sql, **extra}
    return await api.client.post(f"/api/v1/sql/{path}", json=body, headers=headers)


async def test_developers_need_a_per_connection_grant(
    api: Api, services: FakeServices, tenant: uuid.UUID, platform_db: Any
) -> None:
    services.grant_all = False
    source = await api.data_source(tenant)
    developer = services.add_user(tenant, {"developer"})
    for path in ("validate", "execute"):
        refused = await _post(api, developer, path, source)
        assert (
            refused.status_code == 403 and refused.json()["error"]["code"] == "SQL_GRANT_REQUIRED"
        )
    services.sql_grants.add((str(tenant), str(source), str(developer.user_id)))
    executed = await _post(api, developer, "execute", source)
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert [c["name"] for c in body["columns"]] == ["status", "n"] and body["row_count"] > 0
    assert "result_handle" not in body  # rows go to the caller; the handle stays internal

    # Revocation takes effect on the very next query: the grant is never cached.
    checks = services.grant_checks
    services.sql_grants.clear()
    assert (await _post(api, developer, "execute", source)).status_code == 403
    assert services.grant_checks == checks + 1
    purposes = {r["purpose"] for r in await audit_rows(platform_db, tenant)}
    assert purposes == {"sql_editor"}


async def test_org_admins_need_no_grant_and_clients_no_access(
    api: Api, services: FakeServices, tenant: uuid.UUID
) -> None:
    services.grant_all = False
    source = await api.data_source(tenant)
    admin = services.add_user(tenant, {"org_admin"})
    checks = services.grant_checks
    assert (await _post(api, admin, "execute", source)).status_code == 200
    assert services.grant_checks == checks  # exempt: not even asked
    client = services.add_user(tenant, {"client"})
    assert (await _post(api, client, "execute", source)).status_code == 403


async def test_reading_export_sized_results_needs_step_up(
    api: Api, services: FakeServices, tenant: uuid.UUID
) -> None:
    source = await api.data_source(tenant)
    stale = services.add_user(tenant, {"developer"}, fresh_mfa=False)
    refused = await _post(api, stale, "execute", source, max_rows=10_001)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "STEP_UP_REQUIRED"
    assert refused.json()["error"]["details"] == {"method": "any"}
    assert (await _post(api, stale, "execute", source, max_rows=10_000)).status_code == 200
    fresh = services.add_user(tenant, {"developer"})
    assert (await _post(api, fresh, "execute", source, max_rows=10_001)).status_code == 200


def _scoped_user(services: FakeServices, tenant: uuid.UUID, permissions: frozenset[str]) -> Caller:
    caller = services.add_user(tenant, set())
    services.principals[caller.token]["permissions"] = sorted(permissions)
    return caller


async def test_history_is_own_unless_run_debug(
    api: Api, services: FakeServices, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    source = await api.data_source(tenant)
    # `sql:execute` without `run:debug` (e.g. a scoped API key): its own history only.
    # Every role holding `sql:execute` also holds `run:debug` (Section 7.1), so sees all.
    mine = _scoped_user(services, tenant, frozenset({"sql:execute"}))
    theirs = services.add_user(tenant, {"developer"})
    auditor = services.add_user(tenant, {"auditor"})  # run:debug, read-only
    for who in (mine, mine, theirs):
        assert (await _post(api, who, "execute", source)).status_code == 200
    await _post(api, mine, "execute", source, sql="DELETE FROM sales.orders")  # rejected

    def history(who: Caller, **params: Any) -> Any:
        return api.client.get(
            "/api/v1/sql/history",
            params=params,
            headers={**_gateway(api), "Cookie": f"buvi_session={who.token}"},
        )

    own = (await history(mine)).json()["items"]
    assert {i["requested_by"] for i in own} == {str(mine.user_id)} and len(own) == 3
    assert own[0]["status"] == "rejected"  # newest first, rejections included
    first = (await history(mine, limit=2)).json()
    second = (await history(mine, limit=2, cursor=first["next_cursor"])).json()
    assert len(first["items"] + second["items"]) == 3 and second["next_cursor"] is None
    tenant_wide = (await history(auditor)).json()["items"]
    assert len(tenant_wide) == 4
    stranger = services.add_user(other_tenant, {"org_admin"})
    assert (await history(stranger)).json()["items"] == []
    assert (await history(services.add_user(tenant, {"client"}))).status_code == 403


async def test_only_the_gateway_reaches_the_public_routes(
    api: Api, services: FakeServices, tenant: uuid.UUID
) -> None:
    source = await api.data_source(tenant)
    developer = services.add_user(tenant, {"developer"})
    body = {"database_id": str(source), "sql": SQL}
    cookie = {"Cookie": f"buvi_session={developer.token}"}
    wrong_scope = await api.client.post(
        "/api/v1/sql/execute", json=body, headers={**api.service_header(), **cookie}
    )
    assert wrong_scope.status_code == 403  # the internal execute scope is not the proxy scope
    other_caller = await api.client.post(
        "/api/v1/sql/execute",
        json=body,
        headers={
            **api.service_header(subject="analytics-orchestrator", scope="query-gateway:proxy"),
            **cookie,
        },
    )
    assert other_caller.status_code == 403  # sql_editor is api-gateway's purpose only
    no_user = await api.client.post("/api/v1/sql/execute", json=body, headers=_gateway(api))
    assert no_user.status_code == 401
