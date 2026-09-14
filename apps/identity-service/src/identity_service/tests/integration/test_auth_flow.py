"""The Section 6.1 login/logout flow, over HTTP (Phase A1 Definition of Done).

Also covers the cookie attributes the DoD asks to be inspected "at the HTTP
layer": these assertions read the raw `Set-Cookie` header, not a client-side
convenience object, so they see exactly what a browser would.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid

import httpx
import pytest

from identity_service.tests.conftest import Fixtures, StubOidcClient

pytestmark = pytest.mark.integration

TXN_COOKIE = "buvi_oidc_txn"
SESSION_COOKIE = "buvi_session"


def _set_cookie_header(response: httpx.Response, name: str) -> str:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    raise AssertionError(f"no Set-Cookie for {name} in {response.headers.get_list('set-cookie')}")


def _cookie_value(header: str) -> str:
    return header.split("=", 1)[1].split(";", 1)[0]


async def _begin_login(client: httpx.AsyncClient) -> tuple[str, str]:
    """Start the flow; return (state, transaction cookie value)."""
    response = await client.get("/api/v1/auth/login", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    state = re.search(r"state=([^&]+)", location)
    assert state is not None
    return state.group(1), _cookie_value(_set_cookie_header(response, TXN_COOKIE))


# --- Login (Section 6.1 steps 2-6) --------------------------------------------


async def test_login_redirects_with_pkce_s256(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/auth/login", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    assert "code_challenge=" in location
    assert "code_challenge_method=S256" in location or "S256" in location
    assert "state=" in location
    assert "nonce=" in location


async def test_pkce_verifier_never_reaches_the_browser_readable_surface(
    client: httpx.AsyncClient,
) -> None:
    """Section 6.2: the verifier must be HttpOnly, so script cannot read it."""
    response = await client.get("/api/v1/auth/login", follow_redirects=False)
    header = _set_cookie_header(response, TXN_COOKIE)
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "samesite=lax" in header.lower()
    # The verifier must not appear in the redirect URL -- only its S256 hash.
    assert "code_verifier" not in response.headers["location"]


async def test_full_login_sets_a_locked_down_session_cookie(
    client: httpx.AsyncClient, fixtures: Fixtures, oidc: StubOidcClient
) -> None:
    """The DoD's cookie assertion, read from the raw header."""
    tenant_id = await fixtures.create_tenant("login-acme")
    await fixtures.create_user(
        tenant_id=tenant_id,
        email="loginuser@acme.example.com",
        roles=frozenset({"client"}),
        idp_subject="idp-login-1",
    )
    oidc.identity = type(oidc.identity)(
        subject="idp-login-1",
        email="loginuser@acme.example.com",
        display_name="Login User",
        tenant_slug="login-acme",
        roles=frozenset({"client"}),
        raw_claims={},
    )

    state, txn = await _begin_login(client)
    response = await client.get(
        "/api/v1/auth/callback",
        params={"code": "authz-code", "state": state},
        cookies={TXN_COOKIE: txn},
    )
    assert response.status_code == 204

    header = _set_cookie_header(response, SESSION_COOKIE)
    assert "HttpOnly" in header, "Section 6.1: the session cookie must be HttpOnly"
    assert "Secure" in header, "Section 6.1: the session cookie must be Secure"
    assert "samesite=lax" in header.lower(), "Section 6.1: SameSite=Lax"
    assert "Path=/" in header

    # Only the session id -- no token of any kind reaches the browser.
    value = _cookie_value(header)
    assert len(value) >= 43
    with pytest.raises(ValueError):
        uuid.UUID(value)  # an opaque token, not the session row id
    assert "stub-access-token" not in header
    assert "stub-refresh-token" not in header
    assert "stub-refresh-token" not in response.text


async def test_login_rejects_a_mismatched_state(
    client: httpx.AsyncClient, fixtures: Fixtures, oidc: StubOidcClient
) -> None:
    """CSRF defense: a callback whose state does not match is refused."""
    _state, txn = await _begin_login(client)
    response = await client.get(
        "/api/v1/auth/callback",
        params={"code": "authz-code", "state": "attacker-supplied"},
        cookies={TXN_COOKIE: txn},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OIDC_STATE_MISMATCH"
    # The code must not have been spent at the IdP.
    assert oidc.exchange_calls == []


async def test_login_rejects_a_callback_with_no_transaction_cookie(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/auth/callback", params={"code": "c", "state": "s"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OIDC_STATE_MISMATCH"


async def test_login_refuses_a_token_for_an_unknown_tenant(
    client: httpx.AsyncClient, oidc: StubOidcClient
) -> None:
    """A validly signed token for a tenant this platform does not host."""
    oidc.identity = type(oidc.identity)(
        subject="idp-nowhere",
        email="nobody@elsewhere.example.com",
        display_name="Nobody",
        tenant_slug="does-not-exist",
        roles=frozenset({"client"}),
        raw_claims={},
    )
    state, txn = await _begin_login(client)
    response = await client.get(
        "/api/v1/auth/callback",
        params={"code": "authz-code", "state": state},
        cookies={TXN_COOKIE: txn},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "USER_NOT_ACTIVE"


async def test_failed_idp_exchange_leaks_nothing(
    client: httpx.AsyncClient, oidc: StubOidcClient
) -> None:
    """Section 21: no IdP internals in the response."""
    oidc.fail_exchange = True
    state, txn = await _begin_login(client)
    response = await client.get(
        "/api/v1/auth/callback",
        params={"code": "authz-code", "state": state},
        cookies={TXN_COOKIE: txn},
    )
    assert response.status_code == 401
    body = response.text
    assert response.json()["error"]["code"] == "OIDC_EXCHANGE_FAILED"
    assert "realm" not in body.lower()
    assert "client_secret" not in body


# --- Session and logout --------------------------------------------------------


async def test_session_reports_matrix_permissions_not_token_claims(
    client: httpx.AsyncClient, fixtures: Fixtures
) -> None:
    """Section 2: permissions are resolved server-side from stored roles."""
    tenant_id = await fixtures.create_tenant("session-acme")
    user_id = await fixtures.create_user(
        tenant_id=tenant_id, email="dev@acme.example.com", roles=frozenset({"developer"})
    )
    session_id = await fixtures.create_session(tenant_id=tenant_id, user_id=user_id)

    response = await client.get("/api/v1/auth/session", cookies={SESSION_COOKIE: str(session_id)})
    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == str(tenant_id)
    assert body["roles"] == ["developer"]
    assert "sql:execute" in body["permissions"]
    assert "user:manage" not in body["permissions"]
    assert body["step_up_fresh"] is False


async def test_logout_revokes_the_session_and_clears_the_cookie(
    client: httpx.AsyncClient, fixtures: Fixtures, oidc: StubOidcClient
) -> None:
    tenant_id = await fixtures.create_tenant("logout-acme")
    user_id = await fixtures.create_user(
        tenant_id=tenant_id, email="bye@acme.example.com", roles=frozenset({"client"})
    )
    session_id = await fixtures.create_session(tenant_id=tenant_id, user_id=user_id)

    response = await client.post("/api/v1/auth/logout", cookies={SESSION_COOKIE: str(session_id)})
    assert response.status_code == 200
    assert "buvi_session=" in _set_cookie_header(response, SESSION_COOKIE)
    # Revoked at the IdP too (Section 6.1 step 9).
    assert oidc.ended_sessions == ["stub-refresh-token"]

    # The cookie is now dead.
    after = await client.get("/api/v1/auth/session", cookies={SESSION_COOKIE: str(session_id)})
    assert after.status_code == 401


async def test_expired_session_is_refused(client: httpx.AsyncClient, fixtures: Fixtures) -> None:
    """Section 6.9: the absolute lifetime is enforced server-side."""
    tenant_id = await fixtures.create_tenant("expired-acme")
    user_id = await fixtures.create_user(
        tenant_id=tenant_id, email="old@acme.example.com", roles=frozenset({"client"})
    )
    session_id = await fixtures.create_session(
        tenant_id=tenant_id, user_id=user_id, expires_in=dt.timedelta(seconds=-1)
    )
    response = await client.get("/api/v1/auth/session", cookies={SESSION_COOKIE: str(session_id)})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "SESSION_EXPIRED"


async def test_deactivated_user_loses_live_sessions_immediately(
    client: httpx.AsyncClient, fixtures: Fixtures
) -> None:
    """Section 6.7: deprovisioning takes effect now, not at next expiry."""
    tenant_id = await fixtures.create_tenant("deact-acme")
    user_id = await fixtures.create_user(
        tenant_id=tenant_id,
        email="gone@acme.example.com",
        roles=frozenset({"client"}),
        status="deactivated",
    )
    session_id = await fixtures.create_session(tenant_id=tenant_id, user_id=user_id)
    response = await client.get("/api/v1/auth/session", cookies={SESSION_COOKIE: str(session_id)})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "USER_NOT_ACTIVE"


async def test_garbage_session_cookie_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/auth/session", cookies={SESSION_COOKIE: "not-a-uuid"})
    assert response.status_code == 401


# --- Audit (Section 22) ----------------------------------------------------------


async def test_login_and_logout_are_audited(
    client: httpx.AsyncClient, fixtures: Fixtures, oidc: StubOidcClient
) -> None:
    """The DoD requires audit events for login/logout."""
    tenant_id = await fixtures.create_tenant("audit-acme")
    await fixtures.create_user(
        tenant_id=tenant_id,
        email="audited@acme.example.com",
        roles=frozenset({"client"}),
        idp_subject="idp-audit-1",
    )
    oidc.identity = type(oidc.identity)(
        subject="idp-audit-1",
        email="audited@acme.example.com",
        display_name="Audited",
        tenant_slug="audit-acme",
        roles=frozenset({"client"}),
        raw_claims={},
    )

    state, txn = await _begin_login(client)
    login = await client.get(
        "/api/v1/auth/callback",
        params={"code": "authz-code", "state": state},
        cookies={TXN_COOKIE: txn},
    )
    session_cookie = _cookie_value(_set_cookie_header(login, SESSION_COOKIE))
    await client.post("/api/v1/auth/logout", cookies={SESSION_COOKIE: session_cookie})

    events = await fixtures.audit_event_types(tenant_id)
    assert "auth.login" in events
    assert "auth.logout" in events


# --- Hashed session tokens (Section 8.1) -------------------------------------------


async def test_session_token_is_stored_only_as_its_hash_and_never_logged(
    client: httpx.AsyncClient,
    fixtures: Fixtures,
    oidc: StubOidcClient,
    capfd: pytest.CaptureFixture[str],
) -> None:
    import hashlib

    tenant_id = await fixtures.create_tenant("hash-acme")
    user_id = await fixtures.create_user(
        tenant_id=tenant_id,
        email="hashed@acme.example.com",
        roles=frozenset({"client"}),
        idp_subject="idp-hash-1",
    )
    oidc.identity = type(oidc.identity)(
        subject="idp-hash-1",
        email="hashed@acme.example.com",
        display_name="Hashed",
        tenant_slug="hash-acme",
        roles=frozenset({"client"}),
        raw_claims={},
    )
    state, txn = await _begin_login(client)
    login = await client.get(
        "/api/v1/auth/callback",
        params={"code": "authz-code", "state": state},
        cookies={TXN_COOKIE: txn},
    )
    token = _cookie_value(_set_cookie_header(login, SESSION_COOKIE))
    session = await client.get("/api/v1/auth/session", cookies={SESSION_COOKIE: token})
    assert session.status_code == 200

    rows = await fixtures.session_rows(user_id)
    assert len(rows) == 1
    assert rows[0]["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert all(token not in str(value) for value in rows[0].values()), "raw token stored"
    assert token not in session.text, "raw token echoed in a response body"

    captured = capfd.readouterr()
    assert token not in captured.out and token not in captured.err, "raw token logged"


async def test_session_row_id_is_not_a_credential(
    client: httpx.AsyncClient, fixtures: Fixtures
) -> None:
    """The pre-hashing design accepted the row id as the cookie; that must be dead."""
    tenant_id = await fixtures.create_tenant("rowid-acme")
    user_id = await fixtures.create_user(
        tenant_id=tenant_id, email="rowid@acme.example.com", roles=frozenset({"client"})
    )
    handle = await fixtures.create_session(tenant_id=tenant_id, user_id=user_id)

    by_id = await client.get("/api/v1/auth/session", cookies={SESSION_COOKIE: str(handle.id)})
    assert by_id.status_code == 401
    by_token = await client.get("/api/v1/auth/session", cookies={SESSION_COOKIE: str(handle)})
    assert by_token.status_code == 200


async def test_oversized_session_cookie_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/auth/session", cookies={SESSION_COOKIE: "a" * 4096})
    assert response.status_code == 401
