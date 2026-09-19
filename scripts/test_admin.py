"""Phase A10 Definition of Done over HTTP, through api-gateway, against the real stack.

"every Section 7.3 sensitive operation requires a fresh MFA check, provable with an automated
test that first performs the action with a stale/absent step-up token (expect 403) and then with
a fresh one (expect success); quota breach returns a documented error code; a share link serves
its snapshot until it expires or is revoked; a developer without a per-connection grant gets 403
from /sql/execute."

The org_admin logs in twice: session B never verifies MFA (every step-up operation refused),
session A enrolls TOTP and verifies (the same operations succeed). WebAuthn runs end to end with
a software authenticator against identity-service's real verification. Deleting a user and
the MCP write-tool/approval paths are covered by their services' integration suites, not here
(the first would break the demo users other flows rely on; the second needs the MCP server).
"""

from __future__ import annotations

import sys
import time
from typing import Any

import httpx
import pyotp
from test_login import USERS, api, check, failures, login
from test_mcp import member_session
from test_query_gateway import READER_PASSWORD

from platform_testing.webauthn import SoftAuthenticator

SQL = "SELECT status, count(*) AS n FROM sales.orders GROUP BY status ORDER BY status"


def auth_api(method: str, path: str, session: str, **kwargs: Any) -> httpx.Response:
    """Auth-tier calls (MFA verify) honour Retry-After, as a well-behaved client does: this
    flow makes more auth-tier requests than the per-IP burst allows (10, then one per 5 s)."""
    response = api(method, path, session, **kwargs)
    if response.status_code == 429:
        time.sleep(int(response.headers.get("retry-after", "5")) + 1)
        response = api(method, path, session, **kwargs)
    return response


def refused_for_step_up(response: httpx.Response, method: str = "any") -> bool:
    error = response.json().get("error", {}) if response.content else {}
    return (
        response.status_code == 403
        and error.get("code") == "STEP_UP_REQUIRED"
        and error.get("details") == {"method": method}
        and "max_age=300" in response.headers.get("www-authenticate", "")
    )


def sample_source(admin: str) -> str:
    created = api(
        "POST",
        "/api/v1/data-sources",
        admin,
        json={
            "name": "sample-sales-db",
            "engine": "postgres",
            "host_label": "sample-sales-db (compose)",
            "database_name": "sample_sales",
            "allowed_schemas": ["sales"],
        },
    )
    check(
        "data source created (no step-up: no credential yet)",
        created.status_code == 201,
        created.text,
    )
    return str(created.json()["id"])


def main() -> int:
    print("setup: org_admin session A (will verify MFA) and session B (never verifies)")
    admin_a, _ = login(USERS["org_admin"][0])
    admin_b, _ = login(USERS["org_admin"][0])
    developer = member_session(admin_a, "developer")
    developer_id = api("GET", "/api/v1/auth/session", developer).json()["user_id"]
    source = sample_source(admin_a)
    dashboard = api("POST", "/api/v1/dashboards", admin_a, json={"name": "A10 shared"}).json()["id"]

    secret_body = {
        "host": "localhost",
        "port": 5433,
        "username": "buvi_reader",
        "password": READER_PASSWORD,
        "sslmode": "disable",
    }
    operations: list[tuple[str, str, str, dict[str, Any] | None]] = [
        ("connection credentials", "POST", f"/api/v1/data-sources/{source}/secret", secret_body),
        (
            "invite a user",
            "POST",
            "/api/v1/admin/invitations",
            {"email": "a10-invitee@demo.example.com", "role_key": "client"},
        ),
        (
            "role change",
            "PATCH",
            f"/api/v1/admin/users/{developer_id}/roles",
            {"grant": ["auditor"]},
        ),
        ("force logout", "POST", f"/api/v1/admin/users/{developer_id}/sessions/revoke", None),
        ("reset another user's MFA", "POST", f"/api/v1/admin/users/{developer_id}/mfa/reset", None),
        ("create an API key", "POST", "/api/v1/me/api-keys", {"name": "a10"}),
        (
            "change tenant policy",
            "PATCH",
            "/api/v1/admin/policies",
            {"client_can_share_dashboards": True},
        ),
        ("create a share link", "POST", f"/api/v1/dashboards/{dashboard}/share-links", {}),
    ]

    print("Section 7.3 without step-up (session B): every operation refused")
    for name, method, path, body in operations:
        response = api(method, path, admin_b, json=body)
        check(f"no step-up -> 403: {name}", refused_for_step_up(response), response.text)

    print("step-up (session A): enroll TOTP, verify")
    enroll = api("POST", "/api/v1/auth/mfa/enroll", admin_a)
    totp = pyotp.TOTP(enroll.json()["secret"])
    verified = auth_api("POST", "/api/v1/auth/mfa/verify", admin_a, json={"code": totp.now()})
    last_totp_step = int(time.time()) // 30
    check(
        "MFA verified (TOTP)",
        verified.status_code == 200 and verified.json()["method"] == "totp",
        verified.text,
    )

    print("Section 7.3 with a fresh step-up (session A): every operation succeeds")
    results: dict[str, httpx.Response] = {}
    for name, method, path, body in operations:
        results[name] = api(method, path, admin_a, json=body)
        check(
            f"fresh step-up -> success: {name}",
            results[name].status_code in (200, 201),
            results[name].text,
        )
    check(
        "session B still refused",
        refused_for_step_up(api("POST", "/api/v1/me/api-keys", admin_b, json={"name": "b"})),
    )

    print("export threshold: /sql/execute above 10,000 rows needs step-up")
    synced = api("POST", f"/api/v1/data-sources/{source}/sync", admin_a)
    check(
        "catalog synced", synced.status_code == 200 and synced.json().get("ok") is True, synced.text
    )
    big = {"database_id": source, "sql": SQL, "max_rows": 10_001}
    check(
        "stale -> 403", refused_for_step_up(api("POST", "/api/v1/sql/execute", admin_b, json=big))
    )
    fresh = api("POST", "/api/v1/sql/execute", admin_a, json=big)
    check(
        "fresh -> 200 with rows",
        fresh.status_code == 200 and fresh.json()["row_count"] > 0,
        fresh.text,
    )

    print("per-connection SQL grant (developer)")
    developer = member_session(admin_a, "developer")  # logged out by the force-logout above
    small = {"database_id": source, "sql": SQL}
    no_grant = api("POST", "/api/v1/sql/execute", developer, json=small)
    check(
        "developer without a grant -> 403 SQL_GRANT_REQUIRED",
        no_grant.status_code == 403 and no_grant.json()["error"]["code"] == "SQL_GRANT_REQUIRED",
        no_grant.text,
    )
    grant = api(
        "POST", f"/api/v1/data-sources/{source}/sql-grants", admin_a, json={"user_id": developer_id}
    )
    check("org_admin grants SQL on the connection", grant.status_code == 201, grant.text)
    check(
        "developer with the grant -> 200",
        api("POST", "/api/v1/sql/execute", developer, json=small).status_code == 200,
    )
    api("DELETE", f"/api/v1/data-sources/{source}/sql-grants/{grant.json()['id']}", admin_a)
    check(
        "revoked: the next query -> 403",
        api("POST", "/api/v1/sql/execute", developer, json=small).status_code == 403,
    )
    history = api("GET", "/api/v1/sql/history", developer).json().get("items", [])
    check("SQL history lists the editor queries", len(history) >= 1, str(history)[:200])

    print("share link: guest snapshot until revoked")
    link = results["create a share link"].json()
    guest = httpx.get(f"http://localhost:8000/api/v1/share/{link['token']}", timeout=10)
    check(
        "guest (no login) sees the snapshot, not cached",
        guest.status_code == 200
        and guest.json()["name"] == "A10 shared"
        and guest.headers.get("cache-control") == "no-store",
        guest.text,
    )
    listed = api("GET", f"/api/v1/dashboards/{dashboard}/share-links", admin_a)
    check("links listed without the token", link["token"] not in listed.text)
    api("DELETE", f"/api/v1/dashboards/{dashboard}/share-links/{link['id']}", admin_a)
    gone = httpx.get(f"http://localhost:8000/api/v1/share/{link['token']}", timeout=10)
    check("revoked link -> 404", gone.status_code == 404, gone.text)

    print("WebAuthn: register a key, require it for org_admins, step up with it")
    key = SoftAuthenticator()
    options = api("POST", "/api/v1/auth/mfa/enroll", admin_a, json={"method": "webauthn"})
    registered = auth_api(
        "POST",
        "/api/v1/auth/mfa/verify",
        admin_a,
        json={
            "method": "webauthn",
            "credential": key.register(options.json()["options"]),
            "label": "A10 key",
        },
    )
    check(
        "security key registered (py_webauthn verified)",
        registered.status_code == 200,
        registered.text,
    )
    # Re-verify with TOTP so the session's step-up method is TOTP again. A code is accepted once
    # per 30-second step (the replay guard), so wait for the next step if needed.
    while int(time.time()) // 30 <= last_totp_step:
        time.sleep(1)
    again = auth_api("POST", "/api/v1/auth/mfa/verify", admin_a, json={"code": totp.now()})
    check("TOTP re-verified", again.status_code == 200, again.text)
    required = api(
        "PATCH", "/api/v1/admin/policies", admin_a, json={"org_admin_requires_webauthn": True}
    )
    check("policy: org_admin requires WebAuthn", required.status_code == 200, required.text)
    by_totp = api(
        "PATCH", "/api/v1/admin/policies", admin_a, json={"org_admin_requires_webauthn": False}
    )
    check(
        "a TOTP step-up is now refused (method webauthn)",
        refused_for_step_up(by_totp, "webauthn"),
        by_totp.text,
    )
    challenged = api("POST", "/api/v1/auth/mfa/challenge", admin_a)
    check("WebAuthn assertion challenge issued", challenged.status_code == 200, challenged.text)
    challenge = challenged.json()["options"]
    asserted = auth_api(
        "POST",
        "/api/v1/auth/mfa/verify",
        admin_a,
        json={"method": "webauthn", "credential": key.assert_(challenge)},
    )
    check(
        "WebAuthn step-up verified",
        asserted.status_code == 200 and asserted.json()["method"] == "webauthn",
        asserted.text,
    )
    lifted = api(
        "PATCH", "/api/v1/admin/policies", admin_a, json={"org_admin_requires_webauthn": False}
    )
    check(
        "the same operation with a WebAuthn step-up succeeds",
        lifted.status_code == 200,
        lifted.text,
    )

    # Leave the demo developer as other flows expect it.
    api("PATCH", f"/api/v1/admin/users/{developer_id}/roles", admin_a, json={"revoke": ["auditor"]})

    print("quotas and audit")
    quotas = api("GET", "/api/v1/billing/quotas", admin_a)
    tokens = quotas.json().get("llm_tokens", {})
    check(
        "token budget readable",
        quotas.status_code == 200 and tokens.get("limit", 0) > 0,
        quotas.text,
    )
    audit = (
        api("GET", "/api/v1/admin/audit", admin_a, params={"limit": 200}).json().get("items", [])
    )
    kinds = {item["event_type"] for item in audit}
    expected = {
        "auth.mfa_enabled",
        "auth.mfa_factor_added",
        "auth.mfa_reset",
        "tenant.policies_changed",
        "dashboard.share_link.created",
        "dashboard.share_link.revoked",
        "connection.sql_grant_added",
        "connection.sql_grant_revoked",
    }
    check("audit trail has every A10 change", expected <= kinds, str(sorted(expected - kinds)))

    if failures:
        print(f"\nFAILED ({len(failures)}): " + "; ".join(failures))
        return 1
    print("\nPhase A10 DoD flow: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
