"""Invitations, MFA, API keys, self-service sessions and role changes, over HTTP."""

from __future__ import annotations

import datetime as dt
import re
import uuid

import httpx
import pyotp
import pytest

from identity_service.infrastructure.email.sender import InMemoryEmailSender
from identity_service.tests.conftest import (
    FakeLifecycle,
    Fixtures,
    RecordingEvents,
    StubOidcClient,
)

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


def _header_value(response: httpx.Response, name: str) -> str | None:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header.split("=", 1)[1].split(";", 1)[0]
    return None


async def _invite(
    client: httpx.AsyncClient,
    admin_session: str,
    mailbox: InMemoryEmailSender,
    *,
    email: str,
    role: str,
) -> str:
    created = await client.post(
        "/api/v1/admin/invitations",
        json={"email": email, "role_key": role},
        cookies={COOKIE: str(admin_session)},
    )
    assert created.status_code == 201, created.text
    assert "token" not in created.json()
    assert mailbox.sent[-1].to == email
    match = re.search(r"token=([A-Za-z0-9_\-]+)", mailbox.sent[-1].body)
    assert match
    return match.group(1)


async def _accept_as(
    client: httpx.AsyncClient, oidc: StubOidcClient, token: str, *, email: str, subject: str
) -> httpx.Response:
    """POST accept (redirects to the IdP), then complete the callback as `email`."""
    started = await client.post(f"/api/v1/invitations/{token}/accept")
    if started.status_code != 303:
        return started
    state = re.search(r"state=([^&]+)", started.headers["location"])
    txn = _header_value(started, "buvi_oidc_txn")
    assert state and txn
    oidc.identity = type(oidc.identity)(
        subject=subject,
        email=email,
        display_name="Invitee",
        tenant_slug=None,
        roles=frozenset(),
        raw_claims={},
    )
    return await client.get(
        "/api/v1/auth/callback",
        params={"code": "authz-code", "state": state.group(1)},
        cookies={"buvi_oidc_txn": txn},
    )


async def test_invitation_is_redeemed_only_through_a_matching_idp_login(
    client: httpx.AsyncClient,
    fixtures: Fixtures,
    tenant: uuid.UUID,
    mailbox: InMemoryEmailSender,
    oidc: StubOidcClient,
) -> None:
    _, admin_session = await _admin(fixtures, tenant)
    email = f"new-{uuid.uuid4().hex[:6]}@acme.example.com"
    token = await _invite(client, admin_session, mailbox, email=email, role="developer")

    # Email comparison is case-insensitive (Section 6.7).
    accepted = await _accept_as(
        client, oidc, token, email=email.upper(), subject=f"idp-{uuid.uuid4()}"
    )
    assert accepted.status_code == 204, accepted.text
    session_token = _header_value(accepted, COOKIE)
    assert session_token
    me = (await client.get("/api/v1/auth/session", cookies={COOKIE: session_token})).json()
    assert me["roles"] == ["developer"]
    assert me["email"] == email
    assert me["tenant_id"] == str(tenant)

    reused = await client.post(f"/api/v1/invitations/{token}/accept")
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "INVITATION_INVALID"

    events = await fixtures.audit_event_types(tenant)
    assert "user.invited" in events and "user.invitation_accepted" in events


async def test_invitation_refuses_an_idp_login_for_a_different_email(
    client: httpx.AsyncClient,
    fixtures: Fixtures,
    tenant: uuid.UUID,
    mailbox: InMemoryEmailSender,
    oidc: StubOidcClient,
) -> None:
    """Section 6.7: a leaked link cannot be bound to someone else's account."""
    _, admin_session = await _admin(fixtures, tenant)
    email = f"target-{uuid.uuid4().hex[:6]}@acme.example.com"
    token = await _invite(client, admin_session, mailbox, email=email, role="client")

    hijack = await _accept_as(
        client, oidc, token, email="attacker@evil.example.com", subject="idp-attacker"
    )
    assert hijack.status_code == 403
    assert hijack.json()["error"]["code"] == "INVITATION_EMAIL_MISMATCH"
    assert _header_value(hijack, COOKIE) is None

    # The invitation is untouched and still works for the invited person.
    rightful = await _accept_as(client, oidc, token, email=email, subject=f"idp-{uuid.uuid4()}")
    assert rightful.status_code == 204, rightful.text


async def test_unknown_invitation_token_is_invalid(client: httpx.AsyncClient) -> None:
    response = await client.post(f"/api/v1/invitations/{'x' * 43}/accept")
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
    cookies = {
        COOKIE: str(
            await fixtures.create_session(
                tenant_id=tenant, user_id=user, mfa_verified_at=dt.datetime.now(dt.UTC)
            )
        )
    }
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
    cookies = {
        COOKIE: str(
            await fixtures.create_session(
                tenant_id=tenant, user_id=user, mfa_verified_at=dt.datetime.now(dt.UTC)
            )
        )
    }

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

    # A key cannot mint another key: it can never carry a step-up (Section 7.3).
    minted = await client.post("/api/v1/me/api-keys", json={"name": "x"}, headers=bearer)
    assert minted.status_code == 403 and minted.json()["error"]["code"] == "STEP_UP_REQUIRED"

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
    cookies = {
        COOKIE: str(
            await fixtures.create_session(
                tenant_id=tenant, user_id=user, mfa_verified_at=dt.datetime.now(dt.UTC)
            )
        )
    }
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
        COOKIE: str(
            await fixtures.create_session(
                tenant_id=other_tenant, user_id=victim, mfa_verified_at=dt.datetime.now(dt.UTC)
            )
        )
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
    assert {item["id"] for item in listed} == {str(current.id), str(other_device.id)}
    assert [item["current"] for item in listed if item["id"] == str(current.id)] == [True]

    for foreign in (colleague_session.id, stranger_session.id, uuid.uuid4()):
        response = await client.delete(f"/api/v1/me/sessions/{foreign}", cookies=cookies)
        assert response.status_code == 404

    assert (
        await client.delete(f"/api/v1/me/sessions/{other_device.id}", cookies=cookies)
    ).status_code == 204
    assert (
        await client.get("/api/v1/auth/session", cookies={COOKIE: str(other_device)})
    ).status_code == 401


# --- Roles and deletion (Section 2) -------------------------------------------------


async def test_role_change_is_audited_and_published(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID, events: RecordingEvents
) -> None:
    admin, session = await _admin(fixtures, tenant)
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
    # Section 18.1 `identity.role.changed`, published after the response (and the commit).
    [event] = events.role_changes
    assert (event.tenant_id, event.user_id, event.changed_by) == (tenant, target, admin)
    assert (event.roles, event.granted, event.revoked) == (
        ("developer",),
        ("developer",),
        ("client",),
    )

    # A request that changes nothing announces nothing.
    same = await client.patch(
        f"/api/v1/admin/users/{target}/roles",
        json={"grant": ["developer"]},
        cookies={COOKIE: str(session)},
    )
    assert same.status_code == 200 and len(events.role_changes) == 1


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
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID, lifecycle: FakeLifecycle
) -> None:
    _, admin_session = await _admin(fixtures, tenant)
    target = await fixtures.create_user(
        tenant_id=tenant, email="leaver@acme.example.com", roles=frozenset({"developer"})
    )
    target_session = await fixtures.create_session(
        tenant_id=tenant, user_id=target, mfa_verified_at=dt.datetime.now(dt.UTC)
    )
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
    # Section 6.7: the cascade reached dashboard-service (the user's share links).
    assert lifecycle.deactivated == [(tenant, target)]


async def test_a_failed_cascade_deactivates_nothing(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID, lifecycle: FakeLifecycle
) -> None:
    """If a share link cannot be revoked, the deactivation must not commit half-done: the user,
    their sessions and keys stay as they were, and the admin gets a retryable 503."""
    _, admin_session = await _admin(fixtures, tenant)
    target = await fixtures.create_user(
        tenant_id=tenant, email="sharer@acme.example.com", roles=frozenset({"developer"})
    )
    target_session = await fixtures.create_session(tenant_id=tenant, user_id=target)
    lifecycle.fail = True
    refused = await client.delete(
        f"/api/v1/admin/users/{target}", cookies={COOKIE: str(admin_session)}
    )
    assert refused.status_code == 503, refused.text
    assert refused.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert (
        await client.get("/api/v1/auth/session", cookies={COOKIE: str(target_session)})
    ).status_code == 200
    assert "user.deleted" not in await fixtures.audit_event_types(tenant)

    lifecycle.fail = False
    lifecycle.revoked = 3
    done = await client.delete(
        f"/api/v1/admin/users/{target}", cookies={COOKIE: str(admin_session)}
    )
    assert done.status_code == 204


async def test_request_bodies_reject_unknown_fields(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    """Audit check 8: a request body naming a server-controlled field is refused outright rather
    than silently ignored, so a client never believes it set `tenant_id` or `role`."""
    admin, session = await _admin(fixtures, tenant)
    cookies = {COOKIE: str(session)}
    target = await fixtures.create_user(
        tenant_id=tenant, email="strict@acme.example.com", roles=frozenset({"client"})
    )
    other_tenant = await fixtures.create_tenant("strict-other")

    invitation = await client.post(
        "/api/v1/admin/invitations",
        json={
            "email": "new@acme.example.com",
            "role_key": "client",
            "tenant_id": str(other_tenant),
        },
        cookies=cookies,
    )
    assert invitation.status_code == 422, invitation.text

    roles = await client.patch(
        f"/api/v1/admin/users/{target}/roles",
        json={"grant": ["developer"], "user_id": str(admin)},
        cookies=cookies,
    )
    assert roles.status_code == 422, roles.text

    key = await client.post(
        "/api/v1/me/api-keys",
        json={"name": "k", "created_by": str(target)},
        cookies=cookies,
    )
    assert key.status_code == 422, key.text
