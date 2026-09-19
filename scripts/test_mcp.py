"""Phase A9 Definition of Done over HTTP, through api-gateway, against a real MCP server.

"an unapproved MCP server cannot be invoked; an approved server's read tool can be invoked by a
`developer` with a grant and is denied for a user without one; a `write` tool is denied even on
an approved server; every invocation, including denials, is recorded; the SSRF suite is 100%
blocked."

The sample server is the official MCP SDK (FastMCP, Streamable HTTP with SSE responses) on
localhost:8765; localhost is allow-listed for the gateway in dev only. The request-time SSRF
cases (DNS rebinding, redirects, hostile bodies, TLS pinning) need control of DNS and are
proven in mcp-gateway's integration suite; here the registration-time corpus runs over HTTP.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pyotp
from test_dashboards import stream_messages
from test_login import USERS, api, check, failures, login, mailed_token
from test_query_gateway import psql

SAMPLE = "http://localhost:8765/mcp"
#: Sent to the sample server as a bearer token (it ignores it): proves the Vault round trip, and
#: that the token never reaches a response, a log line or a platform database row.
AUTH_TOKEN = f"mcp-live-{uuid.uuid4().hex}"
QUERY = f"refunds-{uuid.uuid4().hex[:8]}"
TOOLS = [
    {"name": "search_docs", "tool_class": "read_metadata"},
    {"name": "lookup_order", "tool_class": "read_data"},
    {"name": "delete_order", "tool_class": "write"},
]


def member_session(admin: str, role: str) -> str:
    """Log in the demo user for `role`, inviting them first on a fresh stack."""
    username, email = USERS[role]
    try:
        session, _ = login(username)
        return session
    except RuntimeError:
        pass
    invite = api(
        "POST", "/api/v1/admin/invitations", admin, json={"email": email, "role_key": role}
    )
    check(f"{role} invited", invite.status_code == 201, invite.text)
    session, _ = login(username, "POST", f"/api/v1/invitations/{mailed_token(email)}/accept")
    return session


def main() -> int:
    print("setup: org_admin with a fresh step-up; developer; client")
    admin, _ = login(USERS["org_admin"][0])
    enroll = api("POST", "/api/v1/auth/mfa/enroll", admin)
    api(
        "POST",
        "/api/v1/auth/mfa/verify",
        admin,
        json={"code": pyotp.TOTP(enroll.json()["secret"]).now()},
    )
    developer = member_session(admin, "developer")
    developer_id = api("GET", "/api/v1/auth/session", developer).json()["user_id"]
    client = member_session(admin, "client")
    denied_before = stream_messages("MCP")

    print("SSRF: registration refuses internal and disguised destinations")
    for url, reason in [
        ("https://169.254.169.254/latest/meta-data", "destination_not_allowed"),
        ("https://10.0.0.1/mcp", "destination_not_allowed"),
        ("https://[::ffff:127.0.0.1]/mcp", "destination_not_allowed"),
        ("https://2130706433/mcp", "numeric_host"),
        ("http://mcp.example.com/mcp", "https_required"),
        ("https://user:pw@mcp.example.com/mcp", "credentials_in_url"),
    ]:
        refused = api(
            "POST",
            "/api/v1/mcp/servers",
            admin,
            json={"name": "ssrf", "endpoint_url": url, "tools": TOOLS[:1]},
        )
        error = refused.json().get("error", {})
        check(
            f"refused {url}",
            refused.status_code == 422
            and error.get("code") == "MCP_ENDPOINT_INVALID"
            and error.get("details") == {"reason": reason},
            refused.text,
        )

    print("register (pending, no network contact), grant, invoke before approval")
    created = api(
        "POST",
        "/api/v1/mcp/servers",
        admin,
        json={
            "name": f"Sample MCP {uuid.uuid4().hex[:6]}",
            "endpoint_url": SAMPLE,
            "auth_token": AUTH_TOKEN,
            "tools": TOOLS,
        },
        headers={"Idempotency-Key": f"a9-{uuid.uuid4()}"},
    )
    check("server registered pending_approval", created.status_code == 201, created.text)
    server = created.json()
    sid = server["id"]
    check(
        "registered pending_approval, token stored by reference only",
        server.get("status") == "pending_approval"
        and server.get("has_auth_token") is True
        and AUTH_TOKEN not in created.text,
        str(server),
    )
    base = f"/api/v1/mcp/servers/{sid}"
    granted = api(
        "POST",
        f"{base}/tools/search_docs/grants",
        admin,
        json={"grantee_user_id": developer_id},
    )
    check("search_docs granted to the developer", granted.status_code == 201, granted.text)
    early = api(
        "POST",
        f"{base}/tools/search_docs/invoke",
        developer,
        json={"arguments": {"query": QUERY}},
    )
    check(
        "unapproved server cannot be invoked (403 MCP_SERVER_NOT_APPROVED)",
        early.status_code == 403 and early.json()["error"]["code"] == "MCP_SERVER_NOT_APPROVED",
        early.text,
    )

    print("approve (org_admin + step-up): discovery verifies the manifest against the live server")
    developer_approve = api("POST", f"{base}/approve", developer)
    check("developer cannot approve", developer_approve.status_code == 403, developer_approve.text)
    approved = api("POST", f"{base}/approve", admin)
    check(
        "approved",
        approved.status_code == 200 and approved.json()["status"] == "approved",
        approved.text,
    )

    print("invoke: grantee allowed, others denied, write denied")
    ok = api(
        "POST",
        f"{base}/tools/search_docs/invoke",
        developer,
        json={"arguments": {"query": QUERY}},
    )
    body = ok.json()
    check("developer with a grant invokes the read tool", ok.status_code == 200, ok.text)
    check(
        "output returned as untrusted data",
        body.get("untrusted") is True
        and "Ignore all previous instructions" in (body.get("content") or [{}])[0].get("text", ""),
        str(body),
    )
    no_grant = api(
        "POST", f"{base}/tools/search_docs/invoke", client, json={"arguments": {"query": "x"}}
    )
    check(
        "user without a grant is denied (403 MCP_TOOL_NOT_GRANTED)",
        no_grant.status_code == 403 and no_grant.json()["error"]["code"] == "MCP_TOOL_NOT_GRANTED",
        no_grant.text,
    )
    write = api(
        "POST", f"{base}/tools/delete_order/invoke", admin, json={"arguments": {"order_id": 1}}
    )
    check(
        "write tool without a grant denied (403 MCP_TOOL_NOT_GRANTED)",
        write.status_code == 403 and write.json()["error"]["code"] == "MCP_TOOL_NOT_GRANTED",
        write.text,
    )
    # Phase A10: a write tool is grantable and invocable, each with a fresh step-up (the
    # admin's step-up is fresh here; the stale cases are in mcp-gateway's integration suite).
    write_grant = api(
        "POST", f"{base}/tools/delete_order/grants", admin, json={"grantee_role": "org_admin"}
    )
    check("write tool granted with step-up", write_grant.status_code == 201, write_grant.text)
    written = api(
        "POST", f"{base}/tools/delete_order/invoke", admin, json={"arguments": {"order_id": 1}}
    )
    check("granted write tool invoked with step-up", written.status_code == 200, written.text)

    print("every invocation recorded; denials audited and published")
    rows = psql(
        "SELECT string_agg(t.tool_name || ':' || i.response_status, ',' ORDER BY i.created_at) "
        "FROM mcp.invocations i JOIN mcp.tools t ON t.id = i.tool_id WHERE t.server_id = :'id'::uuid",
        id=sid,
    )
    check(
        "mcp.invocations has every attempt",
        rows
        == "search_docs:denied,search_docs:ok,search_docs:denied,delete_order:denied,delete_order:ok",
        rows,
    )
    audit = api("GET", "/api/v1/admin/audit", admin, params={"limit": 200}).json().get("items", [])
    kinds = sorted(item["event_type"] for item in audit if item.get("resource_id") == sid)
    check(
        "audit log has registration, approval, invocations and denials",
        kinds
        == sorted(
            [
                "mcp.server.registered",
                "mcp.server.approved",
                "mcp.tool.denied",
                "mcp.tool.invoked",
                "mcp.tool.denied",
                "mcp.tool.denied",
                "mcp.tool.invoked",
            ]
        ),
        str(kinds),
    )
    check(
        "three mcp.invocation.denied events on stream MCP",
        stream_messages("MCP") - denied_before == 3,
        f"{denied_before} -> {stream_messages('MCP')}",
    )

    print("hygiene: no token, argument or tool output in logs or platform rows")
    log_dir = os.environ.get("BUVI_SERVICE_LOGS")
    logs = (
        {p.name: p.read_text(errors="replace") for p in Path(log_dir).glob("*.log")}
        if log_dir
        else {}
    )
    check("service logs captured", "mcp-gateway.log" in logs, str(sorted(logs)))
    leaked = [
        name
        for name, text in logs.items()
        if name != "sample-mcp.log"
        and any(s in text for s in (AUTH_TOKEN, QUERY, "Ignore all previous"))
    ]
    check("no log line carries the token, the arguments or the output", not leaked, str(leaked))
    in_rows = psql(
        "SELECT count(*) FROM mcp.servers WHERE id = :'id'::uuid AND row_to_json(servers)::text LIKE '%' || :'token' || '%'",
        id=sid,
        token=AUTH_TOKEN,
    )
    check("no platform row carries the token", in_rows == "0", in_rows)
    outputs = psql(
        "SELECT count(*) FROM mcp.invocations i JOIN mcp.tools t ON t.id = i.tool_id "
        "WHERE t.server_id = :'id'::uuid AND i.response_summary LIKE '%Ignore all previous%'",
        id=sid,
    )
    check("tool output is never stored", outputs == "0", outputs)

    if failures:
        print(f"\nFAILED ({len(failures)}): " + "; ".join(failures))
        return 1
    print("\nPhase A9 DoD flow: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
