"""Registration, approval, grants and authorization over HTTP (Sections 7.2, 7.3, 9, 14)."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
import pytest

from mcp_gateway.tests.conftest import APP_ROLE_PASSWORD, Harness, PostgresInfo

pytestmark = [pytest.mark.integration, pytest.mark.security]

SERVERS = "/api/v1/mcp/servers"


async def _approved(harness: Harness, tenant: uuid.UUID, **register: Any) -> tuple[Any, str]:
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    server = await harness.register(admin, **register)
    response = await harness.approve(admin, server["id"])
    assert response.status_code == 200, response.text
    return admin, server["id"]


async def test_registration_validates_the_manifest(harness: Harness, tenant: uuid.UUID) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"})
    base = {"name": "Docs", "endpoint_url": "https://mcp.example.com/mcp"}
    duplicate = await harness.client.post(
        SERVERS,
        json={
            **base,
            "tools": [
                {"name": "search_docs", "tool_class": "read_metadata"},
                {"name": "search_docs", "tool_class": "read_data"},
                {"name": "bad name", "tool_class": "read_data"},
            ],
        },
        headers=admin.headers,
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["error"]["code"] == "MCP_MANIFEST_INVALID"
    assert duplicate.json()["error"]["details"]["problems"] == [
        "search_docs: declared twice",
        "'bad name': invalid tool name",
    ]
    unknown_class = await harness.client.post(
        SERVERS,
        json={**base, "tools": [{"name": "x", "tool_class": "execute"}]},
        headers=admin.headers,
    )
    assert unknown_class.status_code == 422
    empty = await harness.client.post(SERVERS, json={**base, "tools": []}, headers=admin.headers)
    assert empty.status_code == 422
    assert harness.network.attempts == []


async def test_who_may_register_list_and_read(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"})
    developer = harness.identity.add_user(tenant, {"developer"})  # no mcp:manage by default
    client = harness.identity.add_user(tenant, {"client"})
    foreign = harness.identity.add_user(other_tenant, {"org_admin"})
    server = await harness.register(admin)
    for who in (developer, client):
        assert (await harness.client.get(SERVERS, headers=who.headers)).status_code == 403
        denied = await harness.client.post(
            SERVERS,
            json={
                "name": "x",
                "endpoint_url": "https://mcp.example.com/mcp",
                "tools": [{"name": "search_docs", "tool_class": "read_metadata"}],
            },
            headers=who.headers,
        )
        assert denied.status_code == 403
    listed = await harness.client.get(SERVERS, headers=admin.headers)
    assert [s["id"] for s in listed.json()["items"]] == [server["id"]]
    assert (await harness.client.get(SERVERS, headers=foreign.headers)).json()["items"] == []
    missing = await harness.client.get(f"{SERVERS}/{server['id']}", headers=foreign.headers)
    assert missing.status_code == 404  # cross-tenant id: 404, never 403


async def test_pagination(harness: Harness, tenant: uuid.UUID) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"})
    ids = sorted([(await harness.register(admin))["id"] for _ in range(3)])
    first = (await harness.client.get(SERVERS, params={"limit": 2}, headers=admin.headers)).json()
    second = (
        await harness.client.get(
            SERVERS, params={"limit": 2, "cursor": first["next_cursor"]}, headers=admin.headers
        )
    ).json()
    assert [s["id"] for s in first["items"] + second["items"]] == ids
    assert second["next_cursor"] is None
    bad = await harness.client.get(SERVERS, params={"cursor": "nope"}, headers=admin.headers)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_CURSOR"


async def test_approval_needs_org_admin_step_up_and_the_same_tenant(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"})
    server = await harness.register(admin)
    stale = await harness.approve(admin, server["id"])
    assert stale.status_code == 403 and "WWW-Authenticate" in stale.headers
    developer = harness.identity.add_user(tenant, {"developer"}, fresh_mfa=True)
    assert (await harness.approve(developer, server["id"])).status_code == 403
    foreign = harness.identity.add_user(other_tenant, {"org_admin"}, fresh_mfa=True)
    assert (await harness.approve(foreign, server["id"])).status_code == 404
    assert harness.network.attempts == []
    fresh = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    assert (await harness.approve(fresh, server["id"])).status_code == 200
    again = await harness.approve(fresh, server["id"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"


async def test_approval_verifies_the_declared_manifest(harness: Harness, tenant: uuid.UUID) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    server = await harness.register(
        admin, tools={"delete_order": "read_data", "not_on_server": "read_metadata"}
    )
    response = await harness.approve(admin, server["id"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MCP_MANIFEST_MISMATCH"
    assert response.json()["error"]["details"]["problems"] == [
        "delete_order: the server marks it as not read-only",
        "not_on_server: not offered by the server",
    ]
    detail = await harness.client.get(f"{SERVERS}/{server['id']}", headers=admin.headers)
    assert detail.json()["status"] == "pending_approval"
    approved = next(e for e in harness.audit.events if e.event_type == "mcp.server.registered")
    assert approved.after_state is not None and "delete_order" in approved.after_state["tools"]


async def test_write_and_admin_tools_need_grants_and_a_fresh_step_up(
    harness: Harness, tenant: uuid.UUID, platform_db: Any
) -> None:
    """Phase A10 DoD: granting and invoking a write tool are step-up operations (Section 7.3),
    and an admin tool is for `org_admin` only (Section 14)."""
    admin, sid = await _approved(
        harness, tenant, tools={"delete_order": "write", "lookup_order": "admin"}
    )
    stale_admin = harness.identity.add_user(tenant, {"org_admin"})
    refused = await harness.grant(stale_admin, sid, "delete_order", grantee_role="developer")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "STEP_UP_REQUIRED"
    assert (
        await harness.grant(admin, sid, "delete_order", grantee_role="developer")
    ).status_code == 201

    calls_before = list(harness.servers.calls)
    stale_dev = harness.identity.add_user(tenant, {"developer"})
    denied = await harness.invoke(stale_dev, sid, "delete_order", {"order_id": 1})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "STEP_UP_REQUIRED"
    assert harness.servers.calls == calls_before  # the write never reached the server
    fresh_dev = harness.identity.add_user(tenant, {"developer"}, fresh_mfa=True)
    done = await harness.invoke(fresh_dev, sid, "delete_order", {"order_id": 1})
    assert done.status_code == 200, done.text
    assert harness.servers.calls[-1] == "delete_order"

    await harness.grant(admin, sid, "lookup_order", grantee_role="developer")
    await harness.grant(admin, sid, "lookup_order", grantee_role="org_admin")
    not_admin = await harness.invoke(fresh_dev, sid, "lookup_order", {"order_id": 2})
    assert not_admin.status_code == 403
    assert not_admin.json()["error"]["code"] == "MCP_TOOL_ADMIN_ONLY"
    by_admin = await harness.invoke(admin, sid, "lookup_order", {"order_id": 2})
    assert by_admin.status_code == 200

    rows = await platform_db.fetch(
        "SELECT response_status, response_summary FROM mcp.invocations i "
        "JOIN mcp.tools t ON t.id = i.tool_id WHERE t.server_id = $1 ORDER BY i.created_at",
        uuid.UUID(sid),
    )
    assert [(r[0], r[1].split(":")[0]) for r in rows] == [
        ("denied", "step_up_required"),
        ("ok", "ok"),
        ("denied", "admin_only"),
        ("ok", "ok"),
    ]
    assert [e.reason for e in harness.events.denied] == ["step_up_required", "admin_only"]


async def test_disable_reject_and_reapprove(
    harness: Harness, tenant: uuid.UUID, platform_db: Any
) -> None:
    admin, sid = await _approved(harness, tenant)
    await harness.grant(admin, sid, "search_docs", grantee_role="org_admin")
    developer = harness.identity.add_user(tenant, {"developer"}, fresh_mfa=True)
    assert (
        await harness.client.post(f"{SERVERS}/{sid}/disable", headers=developer.headers)
    ).status_code == 403

    # Disabling needs no step-up: removing access must never wait on MFA.
    stale_admin = harness.identity.add_user(tenant, {"org_admin"})
    disabled = await harness.client.post(f"{SERVERS}/{sid}/disable", headers=stale_admin.headers)
    assert disabled.status_code == 200 and disabled.json()["status"] == "disabled"
    blocked = await harness.invoke(admin, sid, "search_docs", {"query": "a"})
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "MCP_SERVER_NOT_APPROVED"
    again = await harness.client.post(f"{SERVERS}/{sid}/disable", headers=admin.headers)
    assert again.status_code == 409

    # Re-enabling is an approval: step-up and a fresh check of the live server.
    assert (await harness.approve(stale_admin, sid)).status_code == 403
    reenabled = await harness.approve(admin, sid)
    assert reenabled.status_code == 200 and reenabled.json()["status"] == "approved"
    assert (await harness.invoke(admin, sid, "search_docs", {"query": "a"})).status_code == 200

    pending = await harness.register(admin)
    assert (
        await harness.client.post(f"{SERVERS}/{pending['id']}/disable", headers=admin.headers)
    ).status_code == 409
    rejected = await harness.client.post(f"{SERVERS}/{pending['id']}/reject", headers=admin.headers)
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert (await harness.approve(admin, pending["id"])).status_code == 409  # final
    assert {"mcp.server.disabled", "mcp.server.rejected"} <= set(harness.audit.types())


async def test_undeclared_tools_are_unreachable_and_audited(
    harness: Harness, tenant: uuid.UUID, platform_db: Any
) -> None:
    admin, sid = await _approved(harness, tenant)
    response = await harness.invoke(admin, sid, "delete_order", {"order_id": 1})
    assert response.status_code == 404 and response.json()["error"]["code"] == "MCP_TOOL_NOT_FOUND"
    rows = await platform_db.fetchval(
        "SELECT count(*) FROM mcp.invocations i JOIN mcp.tools t ON t.id = i.tool_id "
        "WHERE t.server_id = $1",
        uuid.UUID(sid),
    )
    assert rows == 0  # no tool_id to record against (Section 14); the audit log has it
    event = harness.audit.events[-1]
    assert event.event_type == "mcp.tool.denied" and event.after_state is not None
    assert event.after_state["tool"] == "delete_order"
    assert event.after_state["summary"] == "unknown_tool"


async def test_grants_lifecycle(harness: Harness, tenant: uuid.UUID) -> None:
    admin, sid = await _approved(harness, tenant)
    developer = harness.identity.add_user(tenant, {"developer"})
    for body in ({}, {"grantee_role": "developer", "grantee_user_id": str(developer.user_id)}):
        bad = await harness.client.post(
            f"{SERVERS}/{sid}/tools/search_docs/grants", json=body, headers=admin.headers
        )
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "MCP_GRANT_INVALID"
    unknown_role = await harness.grant(admin, sid, "search_docs", grantee_role="superuser")
    assert unknown_role.status_code == 422
    assert (await harness.grant(admin, sid, "nope", grantee_role="developer")).status_code == 404
    assert (
        await harness.grant(developer, sid, "search_docs", grantee_role="developer")
    ).status_code == 403

    grant = await harness.grant(admin, sid, "search_docs", grantee_user_id=developer.user_id)
    assert grant.status_code == 201
    duplicate = await harness.grant(admin, sid, "search_docs", grantee_user_id=developer.user_id)
    assert duplicate.status_code == 409
    detail = (await harness.client.get(f"{SERVERS}/{sid}", headers=admin.headers)).json()
    tool = next(t for t in detail["tools"] if t["name"] == "search_docs")
    assert [g["grantee_user_id"] for g in tool["grants"]] == [str(developer.user_id)]
    assert (await harness.invoke(developer, sid, "search_docs", {"query": "a"})).status_code == 200

    grant_id = grant.json()["id"]
    revoke = await harness.client.delete(
        f"{SERVERS}/{sid}/tools/search_docs/grants/{grant_id}", headers=admin.headers
    )
    assert revoke.status_code == 204
    again = await harness.client.delete(
        f"{SERVERS}/{sid}/tools/search_docs/grants/{grant_id}", headers=admin.headers
    )
    assert again.status_code == 404
    after = await harness.invoke(developer, sid, "search_docs", {"query": "a"})
    assert after.status_code == 403 and after.json()["error"]["code"] == "MCP_TOOL_NOT_GRANTED"
    assert harness.audit.types()[-3:] == [
        "mcp.tool.invoked",
        "mcp.tool.grant_revoked",
        "mcp.tool.denied",
    ]


async def test_cross_tenant_invocation_is_404(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    _admin, sid = await _approved(harness, tenant)
    foreign = harness.identity.add_user(other_tenant, {"org_admin"}, fresh_mfa=True)
    response = await harness.invoke(foreign, sid, "search_docs", {"query": "a"})
    assert response.status_code == 404
    grant = await harness.grant(foreign, sid, "search_docs", grantee_role="developer")
    assert grant.status_code == 404


async def test_tool_errors_limits_and_output_caps(
    harness: Harness, tenant: uuid.UUID, platform_db: Any
) -> None:
    admin, sid = await _approved(
        harness,
        tenant,
        tools={"broken_tool": "read_data", "big_report": "read_data", "search_docs": "read_data"},
    )
    for tool in ("broken_tool", "big_report", "search_docs"):
        await harness.grant(admin, sid, tool, grantee_role="org_admin")
    broken = await harness.invoke(admin, sid, "broken_tool")
    assert broken.status_code == 200 and broken.json()["is_error"] is True
    too_big = await harness.invoke(admin, sid, "big_report", {"size": 300_000})
    assert too_big.status_code == 502
    assert too_big.json()["error"]["details"] == {"reason": "response_too_large"}
    huge_args = await harness.invoke(admin, sid, "search_docs", {"query": "x" * 20_000})
    assert huge_args.status_code == 422
    assert huge_args.json()["error"]["code"] == "MCP_ARGUMENTS_TOO_LARGE"
    rows = await platform_db.fetch(
        "SELECT t.tool_name, i.response_status, i.response_summary FROM mcp.invocations i "
        "JOIN mcp.tools t ON t.id = i.tool_id WHERE t.server_id = $1 ORDER BY i.created_at",
        uuid.UUID(sid),
    )
    assert [(r[0], r[1]) for r in rows] == [("broken_tool", "error"), ("big_report", "error")]
    assert rows[1][2] == "upstream:response_too_large"


async def test_invocations_are_append_only_for_the_request_role(
    harness: Harness, tenant: uuid.UUID, postgres: PostgresInfo
) -> None:
    admin, sid = await _approved(harness, tenant)
    await harness.grant(admin, sid, "search_docs", grantee_role="org_admin")
    assert (await harness.invoke(admin, sid, "search_docs", {"query": "a"})).status_code == 200
    conn = await asyncpg.connect(postgres.app_dsn.replace("postgresql+asyncpg", "postgresql"))
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant))
        assert await conn.fetchval("SELECT count(*) FROM mcp.invocations") == 1
        for statement in (
            "UPDATE mcp.invocations SET response_status = 'ok'",
            "DELETE FROM mcp.invocations",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(statement)
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(uuid.uuid4()))
        assert await conn.fetchval("SELECT count(*) FROM mcp.invocations") == 0
        assert await conn.fetchval("SELECT count(*) FROM mcp.tools") == 0  # RLS through servers
    finally:
        await conn.close()
    assert APP_ROLE_PASSWORD  # the app role is the one the service uses


async def test_a_secret_store_failure_is_recorded_not_silent(
    harness: Harness, tenant: uuid.UUID, platform_db: Any
) -> None:
    """An allowed attempt that cannot fetch the server's token is still an invocation row."""
    admin, sid = await _approved(harness, tenant, auth_token="tok-live")
    await harness.grant(admin, sid, "search_docs", grantee_role="org_admin")
    ref = await platform_db.fetchval(
        "SELECT auth_secret_ref FROM mcp.servers WHERE id = $1", uuid.UUID(sid)
    )
    await harness.secrets.delete(ref)
    response = await harness.invoke(admin, sid, "search_docs", {"query": "a"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SECRET_STORE_UNAVAILABLE"
    status = await platform_db.fetchval(
        "SELECT response_status || ':' || response_summary FROM mcp.invocations i "
        "JOIN mcp.tools t ON t.id = i.tool_id WHERE t.server_id = $1",
        uuid.UUID(sid),
    )
    assert status == "error:secret_store_unavailable"
