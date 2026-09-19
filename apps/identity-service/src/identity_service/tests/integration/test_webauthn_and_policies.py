"""Phase A10 over HTTP: WebAuthn ceremonies, step-up by method, MFA removal and reset, tenant
policies and their effect on every principal, and the role catalog.

WebAuthn runs end to end: a software authenticator builds the JSON a browser would send, and
identity-service verifies it with py_webauthn unmodified.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import httpx
import pyotp
import pytest

from identity_service.infrastructure.secrets.store import InMemorySecretStore
from identity_service.tests.conftest import Fixtures
from platform_testing.webauthn import SoftAuthenticator

pytestmark = [pytest.mark.integration, pytest.mark.security]

COOKIE = "buvi_session"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def _user(
    fixtures: Fixtures,
    tenant: uuid.UUID,
    roles: set[str],
    *,
    mfa_verified_at: dt.datetime | None = None,
    mfa_method: str = "totp",
) -> tuple[uuid.UUID, dict[str, str]]:
    user = await fixtures.create_user(
        tenant_id=tenant,
        email=f"u-{uuid.uuid4().hex[:8]}@acme.example.com",
        roles=frozenset(roles),
    )
    session = await fixtures.create_session(
        tenant_id=tenant, user_id=user, mfa_verified_at=mfa_verified_at, mfa_method=mfa_method
    )
    return user, {COOKIE: str(session)}


async def _register_key(
    client: httpx.AsyncClient, cookies: dict[str, str], key: SoftAuthenticator, label: str = "k"
) -> httpx.Response:
    enroll = await client.post(
        "/api/v1/auth/mfa/enroll", json={"method": "webauthn"}, cookies=cookies
    )
    assert enroll.status_code == 201, enroll.text
    assert enroll.json()["method"] == "webauthn" and enroll.json()["secret"] is None
    return await client.post(
        "/api/v1/auth/mfa/verify",
        json={
            "method": "webauthn",
            "credential": key.register(enroll.json()["options"]),
            "label": label,
        },
        cookies=cookies,
    )


async def _webauthn_step_up(
    client: httpx.AsyncClient, cookies: dict[str, str], key: SoftAuthenticator, **knobs: Any
) -> httpx.Response:
    challenge = await client.post("/api/v1/auth/mfa/challenge", cookies=cookies)
    assert challenge.status_code == 200, challenge.text
    return await client.post(
        "/api/v1/auth/mfa/verify",
        json={
            "method": "webauthn",
            "credential": key.assert_(challenge.json()["options"], **knobs),
        },
        cookies=cookies,
    )


async def _session(client: httpx.AsyncClient, cookies: dict[str, str]) -> dict[str, Any]:
    response = await client.get("/api/v1/auth/session", cookies=cookies)
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


# --- WebAuthn ------------------------------------------------------------------------------


async def test_webauthn_first_factor_then_step_up(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID, secrets: InMemorySecretStore
) -> None:
    _, cookies = await _user(fixtures, tenant, {"developer"})
    key = SoftAuthenticator()
    registered = await _register_key(client, cookies, key, label="YubiKey 5C")
    assert registered.status_code == 200, registered.text
    assert registered.json()["method"] == "webauthn" and registered.json()["mfa_enabled"] is True

    factors = (await client.get("/api/v1/me/mfa", cookies=cookies)).json()
    assert [(f["method"], f["label"]) for f in factors] == [("webauthn", "YubiKey 5C")]
    assert "public_key" not in str(factors) and "credential_id" not in str(factors)
    refs = [r for r in secrets.refs() if "/mfa/webauthn/" in r]
    assert len(refs) == 1
    assert set(await secrets.read(refs[0]) or {}) == {"credential_id", "public_key", "sign_count"}

    verified = await _webauthn_step_up(client, cookies, key)
    assert verified.status_code == 200, verified.text
    session = await _session(client, cookies)
    assert session["step_up_fresh"] is True


async def test_a_challenge_is_single_use_and_bound_to_its_ceremony(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    _, cookies = await _user(fixtures, tenant, {"developer"})
    key = SoftAuthenticator()
    assert (await _register_key(client, cookies, key)).status_code == 200

    challenge = (await client.post("/api/v1/auth/mfa/challenge", cookies=cookies)).json()
    assertion = key.assert_(challenge["options"])
    first = await client.post(
        "/api/v1/auth/mfa/verify",
        json={"method": "webauthn", "credential": assertion},
        cookies=cookies,
    )
    assert first.status_code == 200
    replay = await client.post(
        "/api/v1/auth/mfa/verify",
        json={"method": "webauthn", "credential": assertion},
        cookies=cookies,
    )
    assert replay.status_code == 409 and replay.json()["error"]["code"] == "MFA_CHALLENGE_REQUIRED"

    # A failed attempt spends the challenge too: it cannot be retried with a better answer.
    challenge = (await client.post("/api/v1/auth/mfa/challenge", cookies=cookies)).json()
    phished = await client.post(
        "/api/v1/auth/mfa/verify",
        json={
            "method": "webauthn",
            "credential": key.assert_(challenge["options"], origin="https://evil.example.com"),
        },
        cookies=cookies,
    )
    assert phished.status_code == 401
    retry = await client.post(
        "/api/v1/auth/mfa/verify",
        json={"method": "webauthn", "credential": key.assert_(challenge["options"])},
        cookies=cookies,
    )
    assert retry.status_code == 409 and retry.json()["error"]["code"] == "MFA_CHALLENGE_REQUIRED"

    # A registration challenge cannot be spent as an assertion. (The session is fresh from
    # the first verification above, so a second key may be enrolled.)
    enroll = await client.post(
        "/api/v1/auth/mfa/enroll", json={"method": "webauthn"}, cookies=cookies
    )
    assert enroll.status_code == 201
    crossed = await client.post(
        "/api/v1/auth/mfa/verify",
        json={
            "method": "webauthn",
            "credential": key.assert_(
                {"challenge": enroll.json()["options"]["challenge"], "rpId": "localhost"}
            ),
        },
        cookies=cookies,
    )
    assert (
        crossed.status_code == 401 and crossed.json()["error"]["code"] == "MFA_VERIFICATION_FAILED"
    )


@pytest.mark.parametrize(
    "knobs",
    [
        {"origin": "https://evil.example.com"},  # phishing origin
        {"sign_count": 0},  # a counter that did not advance: a cloned key
    ],
    ids=["wrong-origin", "cloned-key"],
)
async def test_bad_assertions_are_refused_and_audited(
    client: httpx.AsyncClient,
    fixtures: Fixtures,
    tenant: uuid.UUID,
    knobs: dict[str, Any],
) -> None:
    _, cookies = await _user(fixtures, tenant, {"developer"})
    key = SoftAuthenticator()
    assert (await _register_key(client, cookies, key)).status_code == 200
    assert (await _webauthn_step_up(client, cookies, key)).status_code == 200  # counter now 1
    refused = await _webauthn_step_up(client, cookies, key, **knobs)
    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "MFA_VERIFICATION_FAILED"
    events = await fixtures.audit_event_types(tenant)
    assert "auth.mfa_verification_failed" in events


async def test_an_unknown_key_is_refused(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    _, cookies = await _user(fixtures, tenant, {"developer"})
    assert (await _register_key(client, cookies, SoftAuthenticator())).status_code == 200
    refused = await _webauthn_step_up(client, cookies, SoftAuthenticator())
    assert refused.status_code == 401


# --- Step-up by method (Section 6.6) -------------------------------------------------------


async def test_platform_super_admin_step_up_must_be_webauthn(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    _, totp = await _user(
        fixtures, tenant, {"platform_super_admin"}, mfa_verified_at=_now(), mfa_method="totp"
    )
    session = await _session(client, totp)
    assert session["step_up_fresh"] is False  # a TOTP check never counts for this role
    assert session["step_up_method"] == "webauthn"  # so the client knows what to prompt for
    _, webauthn = await _user(
        fixtures, tenant, {"platform_super_admin"}, mfa_verified_at=_now(), mfa_method="webauthn"
    )
    assert (await _session(client, webauthn))["step_up_fresh"] is True


async def test_org_admin_webauthn_policy(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    _, admin = await _user(fixtures, tenant, {"org_admin"}, mfa_verified_at=_now())
    target, _ = await _user(fixtures, tenant, {"client"})
    # Requiring WebAuthn while the acting admin has no key would lock them out: refused.
    locked_out = await client.patch(
        "/api/v1/admin/policies", json={"org_admin_requires_webauthn": True}, cookies=admin
    )
    assert locked_out.status_code == 409
    assert locked_out.json()["error"]["code"] == "WEBAUTHN_NOT_ENROLLED"
    assert (await _register_key(client, admin, SoftAuthenticator())).status_code == 200
    patched = await client.patch(
        "/api/v1/admin/policies", json={"org_admin_requires_webauthn": True}, cookies=admin
    )
    assert patched.status_code == 200 and patched.json()["org_admin_requires_webauthn"] is True

    # A TOTP step-up no longer satisfies a Section 7.3 operation for an org_admin.
    _, totp_admin = await _user(fixtures, tenant, {"org_admin"}, mfa_verified_at=_now())
    refused = await client.post(f"/api/v1/admin/users/{target}/sessions/revoke", cookies=totp_admin)
    assert refused.status_code == 403
    assert refused.json()["error"] == {
        **refused.json()["error"],
        "code": "STEP_UP_REQUIRED",
        "details": {"method": "webauthn"},
    }
    # ...but it may still enroll the WebAuthn key it now needs (no deadlock).
    totp_admin_id = uuid.UUID((await _session(client, totp_admin))["user_id"])
    enrolled = await client.post("/api/v1/auth/mfa/enroll", cookies=totp_admin)  # TOTP first
    code = pyotp.TOTP(enrolled.json()["secret"]).now()
    await client.post("/api/v1/auth/mfa/verify", json={"code": code}, cookies=totp_admin)
    key = SoftAuthenticator()
    assert (await _register_key(client, totp_admin, key)).status_code == 200
    assert (await _webauthn_step_up(client, totp_admin, key)).status_code == 200
    allowed = await client.post(f"/api/v1/admin/users/{target}/sessions/revoke", cookies=totp_admin)
    assert allowed.status_code == 200
    assert totp_admin_id


# --- Second factors, removal, admin reset --------------------------------------------------


async def test_a_second_factor_needs_step_up(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    _, cookies = await _user(fixtures, tenant, {"developer"})
    enroll = await client.post("/api/v1/auth/mfa/enroll", cookies=cookies)  # no body: TOTP
    assert enroll.status_code == 201 and enroll.json()["method"] == "totp"
    code = pyotp.TOTP(enroll.json()["secret"]).now()
    assert (
        await client.post("/api/v1/auth/mfa/verify", json={"code": code}, cookies=cookies)
    ).status_code == 200

    # Fresh from that verification: a key may be added now...
    assert (await _register_key(client, cookies, SoftAuthenticator())).status_code == 200
    # ...but a stale session may not add another.
    user_id = uuid.UUID((await _session(client, cookies))["user_id"])
    stale = {
        COOKIE: str(
            await fixtures.create_session(
                tenant_id=tenant, user_id=user_id, mfa_verified_at=_now() - dt.timedelta(minutes=6)
            )
        )
    }
    refused = await client.post(
        "/api/v1/auth/mfa/enroll", json={"method": "webauthn"}, cookies=stale
    )
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "STEP_UP_REQUIRED"
    twice = await client.post("/api/v1/auth/mfa/enroll", json={"method": "totp"}, cookies=cookies)
    assert twice.status_code == 409  # one TOTP factor at most


async def test_removing_factors_needs_step_up_and_clears_secrets(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID, secrets: InMemorySecretStore
) -> None:
    _, cookies = await _user(fixtures, tenant, {"developer"})
    key = SoftAuthenticator()
    assert (await _register_key(client, cookies, key)).status_code == 200
    factor = (await client.get("/api/v1/me/mfa", cookies=cookies)).json()[0]["id"]
    user_id = uuid.UUID((await _session(client, cookies))["user_id"])
    stale = {COOKIE: str(await fixtures.create_session(tenant_id=tenant, user_id=user_id))}
    refused = await client.delete(f"/api/v1/me/mfa/{factor}", cookies=stale)
    assert refused.status_code == 403

    removed = await client.delete(f"/api/v1/me/mfa/{factor}", cookies=cookies)
    assert removed.status_code == 204
    assert (await client.get("/api/v1/me/mfa", cookies=cookies)).json() == []
    assert (await _session(client, cookies))["mfa_enabled"] is False
    assert not [r for r in secrets.refs() if "/mfa/" in r and str(user_id) in r]
    again = await client.delete(f"/api/v1/me/mfa/{factor}", cookies=cookies)
    assert again.status_code == 404


async def test_admin_mfa_reset(
    client: httpx.AsyncClient,
    fixtures: Fixtures,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
) -> None:
    admin_id, admin = await _user(fixtures, tenant, {"org_admin"}, mfa_verified_at=_now())
    target, target_cookies = await _user(fixtures, tenant, {"developer"})
    assert (await _register_key(client, target_cookies, SoftAuthenticator())).status_code == 200
    _, stale_admin = await _user(fixtures, tenant, {"org_admin"})
    assert (
        await client.post(f"/api/v1/admin/users/{target}/mfa/reset", cookies=stale_admin)
    ).status_code == 403
    _, foreign = await _user(fixtures, other_tenant, {"org_admin"}, mfa_verified_at=_now())
    assert (
        await client.post(f"/api/v1/admin/users/{target}/mfa/reset", cookies=foreign)
    ).status_code == 404
    self_reset = await client.post(f"/api/v1/admin/users/{admin_id}/mfa/reset", cookies=admin)
    assert self_reset.status_code == 409

    reset = await client.post(f"/api/v1/admin/users/{target}/mfa/reset", cookies=admin)
    assert reset.status_code == 200 and reset.json() == {
        "factors_revoked": 1,
        "sessions_revoked": 1,
    }
    assert (await client.get("/api/v1/auth/session", cookies=target_cookies)).status_code == 401
    assert "auth.mfa_reset" in await fixtures.audit_event_types(tenant)


# --- Tenant policies and roles -------------------------------------------------------------


async def test_policies_change_effective_permissions_everywhere(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    _, admin = await _user(fixtures, tenant, {"org_admin"}, mfa_verified_at=_now())
    _, client_user = await _user(fixtures, tenant, {"client"})
    _, developer = await _user(fixtures, tenant, {"developer"})
    assert (await client.get("/api/v1/admin/policies", cookies=admin)).json() == {
        "client_can_share_dashboards": False,
        "developer_can_manage_mcp": False,
        "org_admin_requires_webauthn": False,
    }
    assert "dashboard:share" not in (await _session(client, client_user))["permissions"]
    assert "mcp:manage" not in (await _session(client, developer))["permissions"]

    patched = await client.patch(
        "/api/v1/admin/policies",
        json={"client_can_share_dashboards": True, "developer_can_manage_mcp": True},
        cookies=admin,
    )
    assert patched.status_code == 200
    assert "dashboard:share" in (await _session(client, client_user))["permissions"]
    assert "mcp:manage" in (await _session(client, developer))["permissions"]
    assert "mcp:manage" not in (await _session(client, client_user))["permissions"]
    assert "tenant.policies_changed" in await fixtures.audit_event_types(tenant)


async def test_policy_changes_need_policy_manage_and_step_up(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    _, stale_admin = await _user(fixtures, tenant, {"org_admin"})
    _, developer = await _user(fixtures, tenant, {"developer"}, mfa_verified_at=_now())
    body = {"client_can_share_dashboards": True}
    stale = await client.patch("/api/v1/admin/policies", json=body, cookies=stale_admin)
    assert stale.status_code == 403 and stale.json()["error"]["code"] == "STEP_UP_REQUIRED"
    assert (
        await client.patch("/api/v1/admin/policies", json=body, cookies=developer)
    ).status_code == 403
    assert (await client.get("/api/v1/admin/policies", cookies=developer)).status_code == 403
    _, fresh_admin = await _user(fixtures, tenant, {"org_admin"}, mfa_verified_at=_now())
    unknown = await client.patch(
        "/api/v1/admin/policies", json={"everyone_is_admin": True}, cookies=fresh_admin
    )
    assert unknown.status_code == 422
    # Policies are per tenant: another tenant's admin sees its own defaults.
    _, foreign = await _user(fixtures, other_tenant, {"org_admin"}, mfa_verified_at=_now())
    await client.patch("/api/v1/admin/policies", json=body, cookies=foreign)
    assert (await client.get("/api/v1/admin/policies", cookies=stale_admin)).json()[
        "client_can_share_dashboards"
    ] is False


async def test_role_catalog(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    _, admin = await _user(fixtures, tenant, {"org_admin"})
    _, client_user = await _user(fixtures, tenant, {"client"})
    roles = (await client.get("/api/v1/admin/roles", cookies=admin)).json()
    by_key = {r["key"]: r["permissions"] for r in roles}
    assert {"org_admin", "developer", "client", "auditor", "billing_admin"} <= set(by_key)
    assert "platform_super_admin" not in by_key  # not a tenant role
    assert by_key["client"] == ["artifact:read", "chat:use", "dashboard:pin", "dashboard:read"]
    assert (await client.get("/api/v1/admin/roles", cookies=client_user)).status_code == 403


async def test_a_failed_totp_code_is_audited_despite_the_rollback(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    """Regression (found in Phase A10): the failure event was written in the refused request's
    transaction and rolled back with it, so failed MFA checks left no audit trail."""
    _, cookies = await _user(fixtures, tenant, {"developer"})
    enroll = await client.post("/api/v1/auth/mfa/enroll", cookies=cookies)
    good = pyotp.TOTP(enroll.json()["secret"]).now()
    wrong = f"{(int(good) + 1) % 1_000_000:06d}"
    refused = await client.post("/api/v1/auth/mfa/verify", json={"code": wrong}, cookies=cookies)
    assert refused.status_code == 401
    assert "auth.mfa_verification_failed" in await fixtures.audit_event_types(tenant)


async def test_mfa_guesses_are_limited_per_account_not_only_per_ip(
    client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    """Section 24: after 5 failed checks in the window even a correct code is refused (429 with
    Retry-After), whatever address the guesses came from; another account is unaffected."""
    _, cookies = await _user(fixtures, tenant, {"developer"})
    secret = (await client.post("/api/v1/auth/mfa/enroll", cookies=cookies)).json()["secret"]
    good = pyotp.TOTP(secret).now()
    wrong = f"{(int(good) + 1) % 1_000_000:06d}"
    for n in range(5):
        refused = await client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": wrong},
            cookies=cookies,
            headers={"X-Forwarded-For": f"198.51.100.{n}"},
        )
        assert refused.status_code == 401
    locked = await client.post("/api/v1/auth/mfa/verify", json={"code": good}, cookies=cookies)
    assert locked.status_code == 429, locked.text
    assert locked.json()["error"]["code"] == "MFA_TOO_MANY_ATTEMPTS"
    assert int(locked.headers["retry-after"]) > 0

    _, other = await _user(fixtures, tenant, {"developer"})
    other_secret = (await client.post("/api/v1/auth/mfa/enroll", cookies=other)).json()["secret"]
    ok = await client.post(
        "/api/v1/auth/mfa/verify", json={"code": pyotp.TOTP(other_secret).now()}, cookies=other
    )
    assert ok.status_code == 200
