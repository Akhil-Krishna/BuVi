"""Phase A9 DoD over HTTP: registration, approval, grants and invocation against real MCP servers
(the official SDK), in both Streamable HTTP response modes."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from mcp_gateway.tests.conftest import JSON_HOST, SSE_HOST, Harness
from platform_testing.mcp import INJECTION

pytestmark = [pytest.mark.integration, pytest.mark.security]


async def _rows(platform_db: Any, server_id: str) -> list[tuple[str, str, str]]:
    rows = await platform_db.fetch(
        "SELECT t.tool_name, i.response_status, i.response_summary FROM mcp.invocations i "
        "JOIN mcp.tools t ON t.id = i.tool_id WHERE t.server_id = $1 ORDER BY i.created_at",
        uuid.UUID(server_id),
    )
    return [(r["tool_name"], r["response_status"], r["response_summary"]) for r in rows]


@pytest.mark.parametrize("host", [JSON_HOST, SSE_HOST], ids=["json-response", "sse-response"])
async def test_the_dod_journey(
    harness: Harness, tenant: uuid.UUID, platform_db: Any, host: str
) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    developer = harness.identity.add_user(tenant, {"developer"})
    outsider = harness.identity.add_user(tenant, {"developer"})
    server = await harness.register(admin, host=host)
    sid = server["id"]
    assert server["status"] == "pending_approval"
    assert [(t["name"], t["tool_class"], t["default_policy"]) for t in server["tools"]] == [
        ("lookup_order", "read_data", "require_grant"),
        ("search_docs", "read_metadata", "require_grant"),
    ]
    assert harness.network.attempts == []  # registration never touches the network

    # An unapproved server cannot be invoked -- even by a grantee.
    granted = await harness.grant(admin, sid, "search_docs", grantee_user_id=developer.user_id)
    assert granted.status_code == 201, granted.text
    pending = await harness.invoke(developer, sid, "search_docs", {"query": "refunds"})
    assert pending.status_code == 403
    assert pending.json()["error"]["code"] == "MCP_SERVER_NOT_APPROVED"
    assert harness.network.attempts == []

    approved = await harness.approve(admin, sid)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"] == str(admin.user_id)

    ok = await harness.invoke(developer, sid, "search_docs", {"query": "refunds"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["untrusted"] is True and body["is_error"] is False
    assert body["content"] == [
        {"type": "text", "text": f"3 documents mention 'refunds'. {INJECTION}"}
    ]  # returned as data, marked untrusted -- never followed

    denied = await harness.invoke(outsider, sid, "search_docs", {"query": "refunds"})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "MCP_TOOL_NOT_GRANTED"

    role_grant = await harness.grant(admin, sid, "lookup_order", grantee_role="developer")
    assert role_grant.status_code == 201
    structured = await harness.invoke(outsider, sid, "lookup_order", {"order_id": 7})
    assert structured.status_code == 200, structured.text
    assert structured.json()["structured_content"] == {
        "order_id": 7,
        "status": "shipped",
        "amount": 42.5,
    }

    rows = await _rows(platform_db, sid)
    assert [(tool, status) for tool, status, _ in rows] == [
        ("search_docs", "denied"),
        ("search_docs", "ok"),
        ("search_docs", "denied"),
        ("lookup_order", "ok"),
    ]
    assert rows[0][2] == "server_not_approved" and rows[2][2] == "not_granted"
    assert rows[1][2].startswith("ok: 1 text block(s), 0 dropped, ")
    reasons = [(e.reason, e.user_id) for e in harness.events.denied]
    assert reasons == [
        ("server_not_approved", developer.user_id),
        ("not_granted", outsider.user_id),
    ]
    assert harness.audit.types() == [
        "mcp.server.registered",
        "mcp.tool.granted",
        "mcp.tool.denied",
        "mcp.server.approved",
        "mcp.tool.invoked",
        "mcp.tool.denied",
        "mcp.tool.granted",
        "mcp.tool.invoked",
    ]
