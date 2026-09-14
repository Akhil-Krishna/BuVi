"""Invitations, MFA, API keys, self-service sessions and role changes, over HTTP."""

from __future__ import annotations

import datetime as dt
import re
import uuid

import httpx
import pyotp
import pytest

from identity_service.infrastructure.email.sender import InMemoryEmailSender
from identity_service.tests.conftest import Fixtures

pytestmark = pytest.mark.integration

COOKIE = "buvi_session"


async def _admin(fixtures: Fixtures, tenant: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    admin = await fixtures.create_user(
        tenant_id=tenant,
        email=f"admin-{uuid.uuid4().hex[:6]}@acme.example.com",
        roles=frozenset({"org_admin"}),
    )
    session = await fixtures.create_session(
        tenant_id=tenant, user_id=admin, mfa_verified_at=dt.datetime.now(dt.UTC)
    )
    return admin, session


# --- Invitations (Section 6.7) -------------------------------------------------


async def test_invitation_is_emailed_accepted_once_and_grants_the_role(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID, mailbox: InMemoryEmailSender
) -> None:
    _, admin_session = await _admin(fixtures, tenant)
    email = f"new-{uuid.uuid4().hex[:6]}@acme.example.com"

    created = await client.post(
        "/api/v1/admin/invitations",
        json={"email": email, "role_key": "developer"},
        cookies={COOKIE: str(admin_session)},
    )
    assert created.status_code == 201
    assert "token" not in created.json()

    assert len(mailbox.sent) == 1 and mailbox.sent[0].to == email
    match = re.search(r"token=([A-Za-z0-9_\-]+)", mailbox.sent[0].body)
    assert match
    token = match.group(1)

    accepted = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "idp_subject": f"idp-{uuid.uuid4()}"},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["roles"] == ["developer"]
    assert accepted.json()["status"] == "active"

    reused = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "idp_subject": f"idp-{uuid.uuid4()}"},
    )
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "INVITATION_INVALID"

    events = await fixtures.audit_event_types(tenant)
    assert "user.invited" in events and "user.invitation_accepted" in events


async def test_unknown_invitation_token_is_invalid(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": "x" * 43, "idp_subject": "idp-anything"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVITATION_INVALID"


async def test_invite_rejects_a_role_outside_section_2(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    _, session = await _admin(fixtures, tenant)
    response = await client.post(
        "/api/v1/admin/invitations",
        json={"email": "x@acme.example.com", "role_key": "platform_super_admin"},
        cookies={COOKIE: str(session)},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNKNOWN_ROLE"


# --- MFA (Sections 6.6, 7.3) ---------------------------------------------------


async def test_totp_enroll_verify_opens_step_up_and_rejects_replay(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    user = await fixtures.create_user(
        tenant_id=tenant, email="mfa@acme.example.com", roles=frozenset({"client"})
    )
    session = await fixtures.create_session(tenant_id=tenant, user_id=user)
    cookies = {COOKIE: str(session)}

    enrolled = await client.post("/api/v1/auth/mfa/enroll", cookies=cookies)
    assert enrolled.status_code == 201
    code = pyotp.TOTP(enrolled.json()["secret"]).now()

    verified = await client.post("/api/v1/auth/mfa/verify", json={"code": code}, cookies=cookies)
    assert verified.status_code == 200, verified.text
    assert verified.json()["mfa_enabled"] is True

    state = (await client.get("/api/v1/auth/session", cookies=cookies)).json()
    assert state["mfa_enabled"] is True and state["step_up_fresh"] is True

    replay = await client.post("/api/v1/auth/mfa/verify", json={"code": code}, cookies=cookies)
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "MFA_VERIFICATION_FAILED"

    again = await client.post("/api/v1/auth/mfa/enroll", cookies=cookies)
    assert again.status_code == 409


async def test_wrong_totp_code_is_rejected(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    user = await fixtures.create_user(
        tenant_id=tenant, email="mfa2@acme.example.com", roles=frozenset({"client"})
    )
    cookies = {COOKIE: str(await fixtures.create_session(tenant_id=tenant, user_id=user))}
    await client.post("/api/v1/auth/mfa/enroll", cookies=cookies)
    response = await client.post(
        "/api/v1/auth/mfa/verify", json={"code": "000000"}, cookies=cookies
    )
    assert response.status_code == 401


# --- API keys (Section 6.8) -----------------------------------------------------


async def test_api_key_is_shown_once_authenticates_and_dies_on_revoke(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    user = await fixtures.create_user(
        tenant_id=tenant, email="keys@acme.example.com", roles=frozenset({"developer"})
    )
    cookies = {COOKIE: str(await fixtures.create_session(tenant_id=tenant, user_id=user))}

    created = await client.post(
        "/api/v1/me/api-keys", json={"name": "ci", "scopes": ["sql:execute"]}, cookies=cookies
    )
    assert created.status_code == 201
    secret, key_id = created.json()["secret"], created.json()["api_key"]["id"]
    assert secret.startswith("sk_live_")

    listed = await client.get("/api/v1/me/api-keys", cookies=cookies)
    assert secret not in listed.text

    bearer = {"Authorization": f"Bearer {secret}"}
    assert (await client.get("/api/v1/me/api-keys", headers=bearer)).status_code == 200

    # A key cannot mint another key.
    minted = await client.post("/api/v1/me/api-keys", json={"name": "x"}, headers=bearer)
    assert minted.status_code == 401

    assert (
        await client.delete(f"/api/v1/me/api-keys/{key_id}", cookies=cookies)
    ).status_code == 204
    assert (await client.get("/api/v1/me/api-keys", headers=bearer)).status_code == 401


async def test_api_key_scope_cannot_exceed_owner_permissions(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    """A client-role user requesting `user:manage` gets a key without it."""
    user = await fixtures.create_user(
        tenant_id=tenant, email="scope@acme.example.com", roles=frozenset({"client"})
    )
    cookies = {COOKIE: str(await fixtures.create_session(tenant_id=tenant, user_id=user))}
    created = await client.post(
        "/api/v1/me/api-keys", json={"name": "esc", "scopes": ["user:manage"]}, cookies=cookies
    )
    bearer = {"Authorization": f"Bearer {created.json()['secret']}"}
    assert (await client.get("/api/v1/admin/users", headers=bearer)).status_code == 403


async def test_foreign_api_key_revoke_is_404(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    victim = await fixtures.create_user(
        tenant_id=other_tenant, email="v@globex.example.com", roles=frozenset({"developer"})
    )
    victim_cookies = {
        COOKIE: str(await fixtures.create_session(tenant_id=other_tenant, user_id=victim))
    }
    key_id = (
        await client.post("/api/v1/me/api-keys", json={"name": "v"}, cookies=victim_cookies)
    ).json()["api_key"]["id"]

    _, attacker_session = await _admin(fixtures, tenant)
    response = await client.delete(
        f"/api/v1/me/api-keys/{key_id}", cookies={COOKIE: str(attacker_session)}
    )
    assert response.status_code == 404


# --- Self-service sessions (Section 6.9) -----------------------------------------


async def test_user_lists_and_revokes_only_their_own_sessions(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    me = await fixtures.create_user(
        tenant_id=tenant, email="me@acme.example.com", roles=frozenset({"client"})
    )
    colleague = await fixtures.create_user(
        tenant_id=tenant, email="colleague@acme.example.com", roles=frozenset({"client"})
    )
    stranger = await fixtures.create_user(
        tenant_id=other_tenant, email="stranger@globex.example.com", roles=frozenset({"client"})
    )
    current = await fixtures.create_session(tenant_id=tenant, user_id=me)
    other_device = await fixtures.create_session(tenant_id=tenant, user_id=me)
    colleague_session = await fixtures.create_session(tenant_id=tenant, user_id=colleague)
    stranger_session = await fixtures.create_session(tenant_id=other_tenant, user_id=stranger)
    cookies = {COOKIE: str(current)}

    listed = (await client.get("/api/v1/me/sessions", cookies=cookies)).json()["items"]
    assert {item["id"] for item in listed} == {str(current), str(other_device)}
    assert [item["current"] for item in listed if item["id"] == str(current)] == [True]

    for foreign in (colleague_session, stranger_session, uuid.uuid4()):
        response = await client.delete(f"/api/v1/me/sessions/{foreign}", cookies=cookies)
        assert response.status_code == 404

    assert (
        await client.delete(f"/api/v1/me/sessions/{other_device}", cookies=cookies)
    ).status_code == 204
    assert (
        await client.get("/api/v1/auth/session", cookies={COOKIE: str(other_device)})
    ).status_code == 401


# --- Roles and deletion (Section 2) -------------------------------------------------


async def test_role_change_is_audited_with_before_and_after(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    _, session = await _admin(fixtures, tenant)
    target = await fixtures.create_user(
        tenant_id=tenant, email="promote@acme.example.com", roles=frozenset({"client"})
    )
    response = await client.patch(
        f"/api/v1/admin/users/{target}/roles",
        json={"grant": ["developer"], "revoke": ["client"]},
        cookies={COOKIE: str(session)},
    )
    assert response.status_code == 200
    assert response.json()["roles"] == ["developer"]
    assert "user.role_changed" in await fixtures.audit_event_types(tenant)


async def test_admin_cannot_demote_or_delete_themselves(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    admin, session = await _admin(fixtures, tenant)
    cookies = {COOKIE: str(session)}
    demote = await client.patch(
        f"/api/v1/admin/users/{admin}/roles", json={"revoke": ["org_admin"]}, cookies=cookies
    )
    assert demote.status_code == 409
    delete = await client.delete(f"/api/v1/admin/users/{admin}", cookies=cookies)
    assert delete.status_code == 409


async def test_last_active_org_admin_cannot_be_removed(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    """Only one *active* org_admin exists; removing the other admin row is refused."""
    _admin_id, session = await _admin(fixtures, tenant)
    dormant = await fixtures.create_user(
        tenant_id=tenant,
        email="dormant@acme.example.com",
        roles=frozenset({"org_admin"}),
        status="suspended",
    )
    response = await client.patch(
        f"/api/v1/admin/users/{dormant}/roles",
        json={"revoke": ["org_admin"]},
        cookies={COOKIE: str(session)},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LAST_ORG_ADMIN"


async def test_deleting_a_user_revokes_their_sessions_and_keys(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    _, admin_session = await _admin(fixtures, tenant)
    target = await fixtures.create_user(
        tenant_id=tenant, email="leaver@acme.example.com", roles=frozenset({"developer"})
    )
    target_session = await fixtures.create_session(tenant_id=tenant, user_id=target)
    secret = (
        await client.post(
            "/api/v1/me/api-keys", json={"name": "k"}, cookies={COOKIE: str(target_session)}
        )
    ).json()["secret"]

    deleted = await client.delete(
        f"/api/v1/admin/users/{target}", cookies={COOKIE: str(admin_session)}
    )
    assert deleted.status_code == 204
    assert (
        await client.get("/api/v1/auth/session", cookies={COOKIE: str(target_session)})
    ).status_code in (401, 403)
    assert (
        await client.get("/api/v1/me/api-keys", headers={"Authorization": f"Bearer {secret}"})
    ).status_code == 401
    assert "user.deleted" in await fixtures.audit_event_types(tenant)
