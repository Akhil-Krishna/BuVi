"""Phase A9 DoD: the Section 15 SSRF suite is 100% blocked, over HTTP.

Registration refuses what the URL text reveals (the unit corpus, sampled here end to end).
Everything else is decided when the call is made: hostnames resolving to internal addresses,
DNS rebinding after approval, redirects, hostile response bodies, and a certificate for the
wrong name even though the connection is pinned. Where refused, no packet reaches the address.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from mcp_gateway.tests.conftest import JSON_ADDRESS, JSON_HOST, Harness

pytestmark = [pytest.mark.integration, pytest.mark.security]

INTERNAL = ["127.0.0.1", "10.1.2.3", "169.254.169.254", "100.64.0.9", "::1", "::ffff:10.0.0.1"]


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://169.254.169.254/latest/meta-data", "destination_not_allowed"),
        ("https://[::ffff:127.0.0.1]/mcp", "destination_not_allowed"),
        ("https://2130706433/mcp", "numeric_host"),
        ("http://mcp.example.com/mcp", "https_required"),
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("https://user:pw@mcp.example.com/mcp", "credentials_in_url"),
    ],
)
async def test_registration_refuses_what_the_url_reveals(
    harness: Harness, tenant: uuid.UUID, url: str, reason: str
) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"})
    response = await harness.client.post(
        "/api/v1/mcp/servers",
        json={
            "name": "x",
            "endpoint_url": url,
            "tools": [{"name": "search_docs", "tool_class": "read_metadata"}],
        },
        headers=admin.headers,
    )
    assert response.status_code == 422
    assert response.json()["error"] == {
        **response.json()["error"],
        "code": "MCP_ENDPOINT_INVALID",
        "details": {"reason": reason},
    }
    assert harness.network.attempts == []


@pytest.mark.parametrize("internal", INTERNAL)
async def test_a_name_resolving_inside_is_refused_at_approval(
    harness: Harness, tenant: uuid.UUID, internal: str
) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    harness.dns.records["internal.example.com"] = [internal]
    server = await harness.register(admin, host="internal.example.com")
    response = await harness.approve(admin, server["id"])
    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"reason": "destination_not_allowed"}
    assert harness.network.attempts == []
    detail = await harness.client.get(f"/api/v1/mcp/servers/{server['id']}", headers=admin.headers)
    assert detail.json()["status"] == "pending_approval"


async def test_one_internal_address_among_public_ones_is_refused(
    harness: Harness, tenant: uuid.UUID
) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    harness.dns.records["mixed.example.com"] = [JSON_ADDRESS, "10.0.0.7"]
    server = await harness.register(admin, host="mixed.example.com")
    assert (await harness.approve(admin, server["id"])).status_code == 422
    assert harness.network.attempts == []


@pytest.mark.parametrize("rebound", INTERNAL)
async def test_dns_rebinding_after_approval_is_refused_and_recorded(
    harness: Harness, tenant: uuid.UUID, platform_db: Any, rebound: str
) -> None:
    """Approved while the name pointed at a public address; the name then moves inside."""
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    developer = harness.identity.add_user(tenant, {"developer"})
    server = await harness.register(admin)
    assert (await harness.approve(admin, server["id"])).status_code == 200
    await harness.grant(admin, server["id"], "search_docs", grantee_role="developer")
    assert (
        await harness.invoke(developer, server["id"], "search_docs", {"query": "q"})
    ).status_code == 200
    before = len(harness.network.attempts)

    harness.dns.records[JSON_HOST] = [rebound]
    response = await harness.invoke(developer, server["id"], "search_docs", {"query": "q"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DESTINATION_NOT_ALLOWED"
    assert len(harness.network.attempts) == before  # nothing was sent anywhere
    statuses = await platform_db.fetch(
        "SELECT response_status, response_summary FROM mcp.invocations i "
        "JOIN mcp.tools t ON t.id = i.tool_id WHERE t.server_id = $1 ORDER BY i.created_at",
        uuid.UUID(server["id"]),
    )
    assert [(r[0], r[1]) for r in statuses][-1] == ("denied", "destination_not_allowed")
    assert harness.events.denied[-1].reason == "destination_not_allowed"


async def test_a_redirect_is_never_followed(harness: Harness, tenant: uuid.UUID) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    server = await harness.register(admin, path="/moved/mcp")
    response = await harness.approve(admin, server["id"])
    assert response.status_code == 502
    assert response.json()["error"]["details"] == {"reason": "redirect_refused"}
    assert all("169.254" not in a for a in harness.network.attempts)


@pytest.mark.parametrize(
    ("path", "reason"),
    [("/html/mcp", "content_type_not_allowed"), ("/endless/mcp", "response_too_large")],
)
async def test_hostile_response_bodies_are_refused(
    harness: Harness, tenant: uuid.UUID, path: str, reason: str
) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    server = await harness.register(admin, path=path)
    response = await harness.approve(admin, server["id"])
    assert response.status_code == 502
    assert response.json()["error"]["details"] == {"reason": reason}


async def test_pinning_keeps_certificate_verification_against_the_hostname(
    harness: Harness, tenant: uuid.UUID
) -> None:
    """impostor.example.com resolves to the address of a server whose certificate names only
    mcp.example.com: the pinned connection must still fail TLS verification."""
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    harness.dns.records["impostor.example.com"] = [JSON_ADDRESS]
    server = await harness.register(admin, host="impostor.example.com")
    response = await harness.approve(admin, server["id"])
    assert response.status_code == 502
    assert response.json()["error"]["details"] == {"reason": "unreachable"}
    assert harness.network.attempts == [f"{JSON_ADDRESS}:443"]  # connected, then TLS refused


async def test_unresolvable_and_unreachable_hosts(harness: Harness, tenant: uuid.UUID) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    server = await harness.register(admin, host="nowhere.example.com")
    response = await harness.approve(admin, server["id"])
    assert response.json()["error"]["details"] == {"reason": "unresolvable"}
    harness.dns.records["dark.example.com"] = ["93.184.215.99"]  # public, nothing listening
    server = await harness.register(admin, host="dark.example.com")
    response = await harness.approve(admin, server["id"])
    assert response.json()["error"]["details"] == {"reason": "unreachable"}


async def test_the_bearer_token_goes_only_to_its_server(
    harness: Harness, tenant: uuid.UUID, platform_db: Any
) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    token = f"tok-{uuid.uuid4().hex}"
    server = await harness.register(admin, auth_token=token)
    assert server["has_auth_token"] is True and token not in str(server)
    assert (await harness.approve(admin, server["id"])).status_code == 200
    sent = {auth for host, auth in harness.network.authorizations}
    assert sent == {f"Bearer {token}"}
    assert {host for host, _ in harness.network.authorizations} == {JSON_HOST}
    row = await platform_db.fetchrow(
        "SELECT * FROM mcp.servers WHERE id = $1", uuid.UUID(server["id"])
    )
    assert token not in str(dict(row))
    assert row["auth_secret_ref"] == f"mcp/{tenant}/{server['id']}"
    assert await harness.secrets.read(row["auth_secret_ref"]) == {"token": token}
    assert all(token not in str(e.after_state) for e in harness.audit.events)
