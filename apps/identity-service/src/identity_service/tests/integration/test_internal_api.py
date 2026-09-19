"""Service-to-service surface (Section 6.3): token grant, JWKS, introspection, and
the gateway-token check on `/api/v1`."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from identity_service.core.config import (
    SCOPE_AUDIT_WRITE,
    SCOPE_DIRECTORY,
    SCOPE_INTROSPECT,
    SCOPE_PROXY,
    SCOPE_RESOLVE_PRINCIPAL,
    Settings,
)
from identity_service.infrastructure.email.sender import InMemoryEmailSender
from identity_service.infrastructure.secrets.store import InMemorySecretStore
from identity_service.tests.conftest import Fixtures, StubOidcClient

pytestmark = [pytest.mark.integration, pytest.mark.security]

SVC = "X-Service-Authorization"
COOKIE = "buvi_session"
DEV_SECRET = "dev-gateway-secret"


def _token(app: FastAPI, *scopes: str, audience: str = "identity-service") -> str:
    return str(
        app.state.service_token_issuer.issue(
            subject="api-gateway", audience=audience, scopes=frozenset(scopes)
        )
    )


def _grant(**overrides: str) -> dict[str, str]:
    form = {
        "grant_type": "client_credentials",
        "client_id": "api-gateway",
        "client_secret": DEV_SECRET,
        "audience": "identity-service",
        "scope": SCOPE_INTROSPECT,
    }
    form.update(overrides)
    return form


# --- Client-credentials grant ---------------------------------------------------------


async def test_token_grant_issues_a_verifiable_scoped_token(client: httpx.AsyncClient) -> None:
    response = await client.post("/internal/v1/oauth/token", data=_grant())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["scope"] == SCOPE_INTROSPECT
    assert body["expires_in"] == 300


@pytest.mark.parametrize(
    ("overrides", "status", "code"),
    [
        ({"client_secret": "wrong"}, 401, "INVALID_SERVICE_CLIENT"),
        ({"client_id": "nobody"}, 401, "INVALID_SERVICE_CLIENT"),
        ({"audience": "query-gateway"}, 403, "SERVICE_GRANT_NOT_ALLOWED"),
        ({"scope": "identity-service:admin"}, 403, "SERVICE_GRANT_NOT_ALLOWED"),
        ({"grant_type": "password"}, 400, "UNSUPPORTED_GRANT_TYPE"),
    ],
)
async def test_token_grant_refusals(
    client: httpx.AsyncClient, overrides: dict[str, str], status: int, code: str
) -> None:
    response = await client.post("/internal/v1/oauth/token", data=_grant(**overrides))
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert DEV_SECRET not in response.text


async def test_jwks_exposes_only_public_key_material(client: httpx.AsyncClient) -> None:
    keys = (await client.get("/internal/v1/jwks.json")).json()["keys"]
    assert len(keys) == 1
    assert {"n", "e", "kid"} <= set(keys[0])
    assert not {"d", "p", "q", "dp", "dq", "qi"} & set(keys[0])


# --- Introspection ---------------------------------------------------------------------


async def test_introspect_requires_a_service_token_with_the_scope(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    body = {"session_token": "x" * 43}
    assert (await client.post("/internal/v1/introspect", json=body)).status_code == 401
    proxy_only = {SVC: f"Bearer {_token(app, SCOPE_PROXY)}"}
    assert (
        await client.post("/internal/v1/introspect", json=body, headers=proxy_only)
    ).status_code == 403
    wrong_aud = {SVC: f"Bearer {_token(app, SCOPE_INTROSPECT, audience='query-gateway')}"}
    assert (
        await client.post("/internal/v1/introspect", json=body, headers=wrong_aud)
    ).status_code == 401


async def test_introspect_resolves_session_and_api_key(
    client: httpx.AsyncClient, app: FastAPI, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    user = await fixtures.create_user(
        tenant_id=tenant, email="intro@acme.example.com", roles=frozenset({"developer"})
    )
    session = await fixtures.create_session(
        tenant_id=tenant, user_id=user, mfa_verified_at=dt.datetime.now(dt.UTC)
    )
    headers = {SVC: f"Bearer {_token(app, SCOPE_INTROSPECT)}"}

    by_session = await client.post(
        "/internal/v1/introspect", json={"session_token": str(session)}, headers=headers
    )
    assert by_session.status_code == 200, by_session.text
    principal = by_session.json()["principal"]
    assert principal["user_id"] == str(user) and principal["tenant_id"] == str(tenant)
    assert principal["auth_method"] == "session" and "sql:execute" in principal["permissions"]
    assert principal["mfa_verified_at"] is not None

    key = (
        await client.post("/api/v1/me/api-keys", json={"name": "i"}, cookies={COOKIE: str(session)})
    ).json()["secret"]
    by_key = await client.post("/internal/v1/introspect", json={"api_key": key}, headers=headers)
    assert by_key.status_code == 200 and by_key.json()["principal"]["auth_method"] == "api_key"

    bad = await client.post(
        "/internal/v1/introspect", json={"session_token": "y" * 43}, headers=headers
    )
    assert bad.status_code == 401
    both = await client.post(
        "/internal/v1/introspect", json={"session_token": "a", "api_key": "b"}, headers=headers
    )
    assert both.status_code == 422


# --- Gateway token on /api/v1 ------------------------------------------------------------


@asynccontextmanager
async def _client_for(settings: Settings) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    from identity_service.main import create_app

    application = create_app(
        settings=settings,
        secrets=InMemorySecretStore(),
        oidc=StubOidcClient(),
        email=InMemoryEmailSender(),
    )
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=ASGITransport(app=application), base_url="https://identity.test"
        ) as http_client,
    ):
        yield application, http_client


async def test_require_gateway_token_blocks_direct_calls(
    settings: Settings, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    user = await fixtures.create_user(
        tenant_id=tenant, email="gw@acme.example.com", roles=frozenset({"client"})
    )
    session = await fixtures.create_session(tenant_id=tenant, user_id=user)
    enforced = settings.model_copy(update={"require_gateway_token": True})
    async with _client_for(enforced) as (app, http_client):
        cookies = {COOKIE: str(session)}
        direct = await http_client.get("/api/v1/auth/session", cookies=cookies)
        assert direct.status_code == 401
        via_gateway = await http_client.get(
            "/api/v1/auth/session",
            cookies=cookies,
            headers={SVC: f"Bearer {_token(app, SCOPE_PROXY)}"},
        )
        assert via_gateway.status_code == 200
        # Health stays reachable for probes.
        assert (await http_client.get("/health/live")).status_code == 200


async def test_present_service_token_is_always_verified(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    forged = await client.get("/api/v1/auth/session", headers={SVC: "Bearer not-a-jwt"})
    assert forged.status_code == 401
    unscoped = await client.get(
        "/api/v1/auth/session", headers={SVC: f"Bearer {_token(app, SCOPE_INTROSPECT)}"}
    )
    assert unscoped.status_code == 403


async def test_forwarded_ip_is_trusted_only_from_the_gateway(
    client: httpx.AsyncClient, app: FastAPI, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    user = await fixtures.create_user(
        tenant_id=tenant, email="ip@acme.example.com", roles=frozenset({"client"})
    )
    cookies = {
        COOKIE: str(
            await fixtures.create_session(
                tenant_id=tenant, user_id=user, mfa_verified_at=dt.datetime.now(dt.UTC)
            )
        )
    }

    spoofed = await client.post(
        "/api/v1/me/api-keys",
        json={"name": "a"},
        cookies=cookies,
        headers={"X-Forwarded-For": "198.51.100.9"},
    )
    assert spoofed.status_code == 201
    assert await fixtures.latest_audit_ip(tenant, "api_key.created") != "198.51.100.9"

    proxied = await client.post(
        "/api/v1/me/api-keys",
        json={"name": "b"},
        cookies=cookies,
        headers={"X-Forwarded-For": "203.0.113.7", SVC: f"Bearer {_token(app, SCOPE_PROXY)}"},
    )
    assert proxied.status_code == 201
    assert await fixtures.latest_audit_ip(tenant, "api_key.created") == "203.0.113.7"


# --- Audit events recorded on behalf of other services (ADR 0004) ---------------------


def _service_token(app: FastAPI, subject: str, *scopes: str) -> str:
    return str(
        app.state.service_token_issuer.issue(
            subject=subject, audience="identity-service", scopes=frozenset(scopes)
        )
    )


async def _audit_rows(fixtures: Fixtures, tenant: uuid.UUID) -> list[dict[str, object]]:
    from sqlalchemy import text

    async with fixtures._factory() as session:
        result = await session.execute(
            text(
                "SELECT event_type, actor_user_id, resource_type, resource_id, before_state, "
                "after_state, request_id, host(ip_address) AS ip FROM identity.audit_events "
                "WHERE tenant_id = :tid ORDER BY created_at"
            ),
            {"tid": str(tenant)},
        )
        return [dict(row._mapping) for row in result]


@pytest.mark.parametrize(
    ("subject", "scope"), [("metadata-service", SCOPE_AUDIT_WRITE)], ids=["metadata-service"]
)
async def test_token_grant_for_metadata_service_audit_scope(
    client: httpx.AsyncClient, subject: str, scope: str
) -> None:
    allowed = await client.post(
        "/internal/v1/oauth/token",
        data=_grant(client_id=subject, client_secret="dev-metadata-secret", scope=scope),
    )
    assert allowed.status_code == 200, allowed.text
    refused = await client.post(
        "/internal/v1/oauth/token",
        data=_grant(client_id=subject, client_secret="dev-metadata-secret", scope=SCOPE_PROXY),
    )
    assert refused.status_code == 403


async def test_service_records_audit_event_with_redaction_and_request_id(
    app: FastAPI, client: httpx.AsyncClient, fixtures: Fixtures, tenant: uuid.UUID
) -> None:
    actor = await fixtures.create_user(
        tenant_id=tenant, email="dev@acme.example.com", roles=frozenset({"developer"})
    )
    response = await client.post(
        "/internal/v1/audit-events",
        headers={
            SVC: f"Bearer {_service_token(app, 'metadata-service', SCOPE_AUDIT_WRITE)}",
            "X-Request-ID": "req_metadata_0001",
        },
        json={
            "tenant_id": str(tenant),
            "actor_user_id": str(actor),
            "event_type": "connection.secret_rotated",
            "resource_type": "data_source",
            "resource_id": "ds-1",
            "before_state": {"status": "active"},
            "after_state": {"status": "pending", "password": "hunter2-audit"},
            "ip_address": "203.0.113.9",
        },
    )
    assert response.status_code == 204, response.text
    row = (await _audit_rows(fixtures, tenant))[-1]
    assert row["event_type"] == "connection.secret_rotated"
    assert row["actor_user_id"] == actor
    assert row["request_id"] == "req_metadata_0001"
    assert row["ip"] == "203.0.113.9"
    assert row["after_state"] == {"status": "pending", "password": "[REDACTED]"}
    assert "hunter2-audit" not in str(row)


@pytest.mark.parametrize(
    ("subject", "scopes", "event_type", "status", "code"),
    [
        (
            "metadata-service",
            (SCOPE_AUDIT_WRITE,),
            "user.role_changed",
            403,
            "AUDIT_EVENT_NOT_ALLOWED",
        ),
        ("api-gateway", (SCOPE_AUDIT_WRITE,), "connection.created", 403, "AUDIT_EVENT_NOT_ALLOWED"),
        ("metadata-service", (SCOPE_INTROSPECT,), "connection.created", 403, "FORBIDDEN"),
        (None, (), "connection.created", 401, "AUTHENTICATION_REQUIRED"),
    ],
    ids=["foreign-namespace", "client-without-namespace", "missing-scope", "no-token"],
)
async def test_audit_event_refusals_write_nothing(
    app: FastAPI,
    client: httpx.AsyncClient,
    fixtures: Fixtures,
    tenant: uuid.UUID,
    subject: str | None,
    scopes: tuple[str, ...],
    event_type: str,
    status: int,
    code: str,
) -> None:
    headers = {SVC: f"Bearer {_service_token(app, subject, *scopes)}"} if subject else {}
    response = await client.post(
        "/internal/v1/audit-events",
        headers=headers,
        json={"tenant_id": str(tenant), "event_type": event_type},
    )
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert await _audit_rows(fixtures, tenant) == []


async def test_token_grant_for_query_gateway_is_limited_to_its_audiences(
    client: httpx.AsyncClient,
) -> None:
    def grant(audience: str, scope: str) -> dict[str, str]:
        return _grant(
            client_id="query-gateway",
            client_secret="dev-query-gateway-secret",
            audience=audience,
            scope=scope,
        )

    policy = await client.post(
        "/internal/v1/oauth/token", data=grant("metadata-service", "metadata-service:query-policy")
    )
    assert policy.status_code == 200, policy.text
    introspect = await client.post(
        "/internal/v1/oauth/token", data=grant("identity-service", SCOPE_INTROSPECT)
    )
    assert introspect.status_code == 200
    for audience, scope in (
        ("identity-service", SCOPE_AUDIT_WRITE),
        ("metadata-service", "metadata-service:proxy"),
        ("query-gateway", "query-gateway:execute"),
    ):
        refused = await client.post("/internal/v1/oauth/token", data=grant(audience, scope))
        assert refused.status_code == 403, (audience, scope)


# --- Delegated principal resolution (ADR 0006) -------------------------------------------------


async def test_resolve_principal_returns_current_roles_never_step_up(
    app: FastAPI,
    client: httpx.AsyncClient,
    fixtures: Fixtures,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
) -> None:
    user = await fixtures.create_user(
        tenant_id=tenant, email="chat@acme.example.com", roles=frozenset({"client"})
    )
    headers = {
        SVC: f"Bearer {_service_token(app, 'analytics-orchestrator', SCOPE_RESOLVE_PRINCIPAL)}"
    }
    response = await client.post(
        "/internal/v1/principals/resolve",
        headers=headers,
        json={"tenant_id": str(tenant), "user_id": str(user)},
    )
    assert response.status_code == 200, response.text
    principal = response.json()["principal"]
    assert principal["user_id"] == str(user) and principal["tenant_id"] == str(tenant)
    assert "chat:use" in principal["permissions"] and "sql:execute" not in principal["permissions"]
    assert principal["auth_method"] == "service_jwt"
    assert principal["mfa_verified"] is False and principal["mfa_verified_at"] is None

    foreign = await client.post(
        "/internal/v1/principals/resolve",
        headers=headers,
        json={"tenant_id": str(other_tenant), "user_id": str(user)},
    )
    assert foreign.status_code == 404
    inactive = await fixtures.create_user(
        tenant_id=tenant,
        email="gone@acme.example.com",
        roles=frozenset({"client"}),
        status="deactivated",
    )
    refused = await client.post(
        "/internal/v1/principals/resolve",
        headers=headers,
        json={"tenant_id": str(tenant), "user_id": str(inactive)},
    )
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "USER_NOT_ACTIVE"
    wrong_scope = {SVC: f"Bearer {_service_token(app, 'analytics-orchestrator', SCOPE_INTROSPECT)}"}
    denied = await client.post(
        "/internal/v1/principals/resolve",
        headers=wrong_scope,
        json={"tenant_id": str(tenant), "user_id": str(user)},
    )
    assert denied.status_code == 403


@pytest.mark.parametrize(
    ("client_id", "secret", "audience", "scope", "allowed"),
    [
        (
            "analytics-orchestrator",
            "dev-analytics-orchestrator-secret",
            "query-gateway",
            "query-gateway:execute",
            True,
        ),
        (
            "analytics-orchestrator",
            "dev-analytics-orchestrator-secret",
            "metadata-service",
            "metadata-service:context",
            True,
        ),
        (
            "analytics-orchestrator",
            "dev-analytics-orchestrator-secret",
            "metadata-service",
            "metadata-service:query-policy",
            False,
        ),
        (
            "worker-runtime",
            "dev-worker-runtime-secret",
            "analytics-orchestrator",
            "analytics-orchestrator:execute",
            True,
        ),
        (
            "worker-runtime",
            "dev-worker-runtime-secret",
            "query-gateway",
            "query-gateway:execute",
            False,
        ),
        (
            "api-gateway",
            "dev-gateway-secret",
            "analytics-orchestrator",
            "analytics-orchestrator:events",
            True,
        ),
        (
            "api-gateway",
            "dev-gateway-secret",
            "analytics-orchestrator",
            "analytics-orchestrator:execute",
            False,
        ),
    ],
)
async def test_analytics_service_clients_get_only_their_scopes(
    client: httpx.AsyncClient, client_id: str, secret: str, audience: str, scope: str, allowed: bool
) -> None:
    response = await client.post(
        "/internal/v1/oauth/token",
        data=_grant(client_id=client_id, client_secret=secret, audience=audience, scope=scope),
    )
    assert (response.status_code == 200) is allowed, response.text


async def test_directory_lists_a_tenants_users_by_id_or_role(
    app: FastAPI,
    client: httpx.AsyncClient,
    fixtures: Fixtures,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
) -> None:
    admin = await fixtures.create_user(
        tenant_id=tenant, email="dir-admin@acme.example.com", roles=frozenset({"org_admin"})
    )
    member = await fixtures.create_user(
        tenant_id=tenant, email="dir-dev@acme.example.com", roles=frozenset({"developer"})
    )
    stranger = await fixtures.create_user(
        tenant_id=other_tenant, email="dir-x@other.example.com", roles=frozenset({"org_admin"})
    )
    headers = {SVC: f"Bearer {_service_token(app, 'notification-service', SCOPE_DIRECTORY)}"}

    admins = await client.post(
        "/internal/v1/directory/users",
        headers=headers,
        json={"tenant_id": str(tenant), "role": "org_admin"},
    )
    assert admins.status_code == 200, admins.text
    assert [(u["id"], u["email"], u["roles"]) for u in admins.json()["users"]] == [
        (str(admin), "dir-admin@acme.example.com", ["org_admin"])
    ]
    # Ids from another tenant simply do not match (RLS-bound to tenant_id).
    by_id = await client.post(
        "/internal/v1/directory/users",
        headers=headers,
        json={"tenant_id": str(tenant), "user_ids": [str(member), str(stranger)]},
    )
    assert [u["id"] for u in by_id.json()["users"]] == [str(member)]
    assert by_id.json()["users"][0]["status"] == "active"

    wrong_scope = {SVC: f"Bearer {_service_token(app, 'notification-service', SCOPE_INTROSPECT)}"}
    refused = await client.post(
        "/internal/v1/directory/users", headers=wrong_scope, json={"tenant_id": str(tenant)}
    )
    assert refused.status_code == 403
    assert (await client.post("/internal/v1/directory/users", json={})).status_code == 401


async def test_seats_are_exact_while_the_recipient_list_says_when_it_is_capped(
    app: FastAPI,
    client: httpx.AsyncClient,
    fixtures: Fixtures,
    tenant: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seats past the directory cap were undercounted (ADR 0014): seats now come from an exact
    count, and a capped list is flagged `truncated` instead of passing as complete."""
    from identity_service.api import internal

    monkeypatch.setattr(internal, "MAX_DIRECTORY_USERS", 2)
    for n in range(3):
        await fixtures.create_user(
            tenant_id=tenant, email=f"seat{n}@acme.example.com", roles=frozenset({"client"})
        )
    await fixtures.create_user(
        tenant_id=tenant,
        email="gone@acme.example.com",
        roles=frozenset({"client"}),
        status="deactivated",
    )
    headers = {SVC: f"Bearer {_service_token(app, 'analytics-orchestrator', SCOPE_DIRECTORY)}"}
    seats = await client.post(
        "/internal/v1/directory/seats", headers=headers, json={"tenant_id": str(tenant)}
    )
    assert seats.status_code == 200 and seats.json() == {"active_users": 3}
    listed = (
        await client.post(
            "/internal/v1/directory/users", headers=headers, json={"tenant_id": str(tenant)}
        )
    ).json()
    assert len(listed["users"]) == 2 and listed["truncated"] is True
    by_role = (
        await client.post(
            "/internal/v1/directory/users",
            headers=headers,
            json={"tenant_id": str(tenant), "role": "org_admin"},
        )
    ).json()
    assert by_role == {"users": [], "truncated": False}
