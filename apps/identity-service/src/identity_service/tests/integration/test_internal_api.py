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

from identity_service.core.config import SCOPE_INTROSPECT, SCOPE_PROXY, Settings
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
    cookies = {COOKIE: str(await fixtures.create_session(tenant_id=tenant, user_id=user))}

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
