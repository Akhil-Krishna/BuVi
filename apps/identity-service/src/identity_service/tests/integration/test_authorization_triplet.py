"""The Section 25 authorization triplet, for every protected endpoint.

    "every endpoint that accepts a user-supplied identifier gets a
     same-tenant-allowed / cross-tenant-404 / no-permission-403 test triplet
     at minimum"

Endpoints are enumerated in one table so that adding a route without adding its
authorization tests is a visible omission rather than a silent one.

The cross-tenant case asserts `404`, never `403` -- Section 7.2 requires that a
caller cannot distinguish "exists but is not yours" from "does not exist".
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from identity_service.tests.conftest import Fixtures

pytestmark = [pytest.mark.integration, pytest.mark.security]


@dataclass(frozen=True)
class ProtectedEndpoint:
    """One row of the Section 9 catalog, with what it takes to call it."""

    name: str
    method: str
    #: `{user_id}` is substituted with the target user's id.
    path: str
    #: Roles that Section 7.1 says may call this endpoint.
    allowed_roles: frozenset[str]
    #: A role that holds a session but not the permission.
    denied_role: str
    body: dict[str, Any] | None = None
    #: True when Section 9 marks the endpoint step-up (Section 7.3).
    step_up: bool = False
    #: True when the path carries a resource id, so the 404 case applies.
    takes_resource_id: bool = True
    expected_success: frozenset[int] = field(default_factory=lambda: frozenset({200, 201, 204}))


ENDPOINTS: list[ProtectedEndpoint] = [
    ProtectedEndpoint(
        name="read_user",
        method="GET",
        path="/api/v1/admin/users/{user_id}",
        allowed_roles=frozenset({"org_admin"}),
        denied_role="developer",
    ),
    ProtectedEndpoint(
        name="change_roles",
        method="PATCH",
        path="/api/v1/admin/users/{user_id}/roles",
        allowed_roles=frozenset({"org_admin"}),
        denied_role="developer",
        body={"grant": ["developer"], "revoke": []},
        step_up=True,
    ),
    ProtectedEndpoint(
        name="revoke_user_sessions",
        method="POST",
        path="/api/v1/admin/users/{user_id}/sessions/revoke",
        allowed_roles=frozenset({"org_admin"}),
        denied_role="developer",
        step_up=True,
    ),
    ProtectedEndpoint(
        name="delete_user",
        method="DELETE",
        path="/api/v1/admin/users/{user_id}",
        allowed_roles=frozenset({"org_admin"}),
        denied_role="developer",
        step_up=True,
    ),
]

# Endpoints with no resource id in the path: the permission half of the triplet
# still applies, the 404 half does not.
COLLECTION_ENDPOINTS: list[ProtectedEndpoint] = [
    ProtectedEndpoint(
        name="list_users",
        method="GET",
        path="/api/v1/admin/users",
        allowed_roles=frozenset({"org_admin"}),
        denied_role="client",
        takes_resource_id=False,
    ),
    ProtectedEndpoint(
        name="list_invitations",
        method="GET",
        path="/api/v1/admin/invitations",
        allowed_roles=frozenset({"org_admin"}),
        denied_role="developer",
        takes_resource_id=False,
    ),
    ProtectedEndpoint(
        name="create_invitation",
        method="POST",
        path="/api/v1/admin/invitations",
        allowed_roles=frozenset({"org_admin"}),
        denied_role="developer",
        body={"email": "invitee@acme.example.com", "role_key": "client"},
        step_up=True,
        takes_resource_id=False,
    ),
    ProtectedEndpoint(
        name="list_audit",
        method="GET",
        path="/api/v1/admin/audit",
        allowed_roles=frozenset({"org_admin", "auditor"}),
        denied_role="developer",
        takes_resource_id=False,
    ),
    ProtectedEndpoint(
        name="list_my_sessions",
        method="GET",
        path="/api/v1/me/sessions",
        allowed_roles=frozenset({"client", "developer", "org_admin"}),
        denied_role="",
        takes_resource_id=False,
    ),
    ProtectedEndpoint(
        name="read_session",
        method="GET",
        path="/api/v1/auth/session",
        allowed_roles=frozenset({"client", "developer", "org_admin"}),
        denied_role="",
        takes_resource_id=False,
    ),
    ProtectedEndpoint(
        name="mfa_enroll",
        method="POST",
        path="/api/v1/auth/mfa/enroll",
        allowed_roles=frozenset({"client", "developer", "org_admin"}),
        denied_role="",
        takes_resource_id=False,
    ),
    ProtectedEndpoint(
        name="create_my_api_key",
        method="POST",
        path="/api/v1/me/api-keys",
        allowed_roles=frozenset({"client", "developer", "org_admin"}),
        denied_role="",
        body={"name": "ci", "scopes": ["chat:use"]},
        takes_resource_id=False,
    ),
    ProtectedEndpoint(
        name="logout",
        method="POST",
        path="/api/v1/auth/logout",
        allowed_roles=frozenset({"client", "developer", "org_admin"}),
        denied_role="",
        takes_resource_id=False,
    ),
    ProtectedEndpoint(
        name="list_my_api_keys",
        method="GET",
        path="/api/v1/me/api-keys",
        allowed_roles=frozenset({"client", "developer", "org_admin"}),
        denied_role="",
        takes_resource_id=False,
    ),
]

ALL_ENDPOINTS = ENDPOINTS + COLLECTION_ENDPOINTS

FRESH_MFA = dt.timedelta(seconds=0)


async def _call(
    client: httpx.AsyncClient,
    endpoint: ProtectedEndpoint,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> httpx.Response:
    path = endpoint.path.format(user_id=user_id) if user_id else endpoint.path
    return await client.request(
        endpoint.method,
        path,
        json=endpoint.body,
        cookies={"buvi_session": str(session_id)},
    )


# --- 1. Same tenant, right permission -> allowed ------------------------------


@pytest.mark.parametrize("endpoint", ALL_ENDPOINTS, ids=lambda e: e.name)
async def test_same_tenant_with_permission_is_allowed(
    endpoint: ProtectedEndpoint,
    client: httpx.AsyncClient,
    fixtures: Fixtures,
    tenant: uuid.UUID,
) -> None:
    role = sorted(endpoint.allowed_roles)[0]
    actor = await fixtures.create_user(
        tenant_id=tenant, email=f"{endpoint.name}-actor@acme.example.com", roles=frozenset({role})
    )
    # A second org_admin so last-admin protection does not mask the auth result.
    await fixtures.create_user(
        tenant_id=tenant,
        email=f"{endpoint.name}-spare@acme.example.com",
        roles=frozenset({"org_admin"}),
    )
    target = await fixtures.create_user(
        tenant_id=tenant,
        email=f"{endpoint.name}-target@acme.example.com",
        roles=frozenset({"client"}),
    )
    session_id = await fixtures.create_session(
        tenant_id=tenant, user_id=actor, mfa_verified_at=dt.datetime.now(dt.UTC)
    )

    response = await _call(client, endpoint, session_id=session_id, user_id=target)
    assert response.status_code in endpoint.expected_success, (
        f"{endpoint.name}: expected success, got {response.status_code} {response.text}"
    )


# --- 2. Cross tenant -> 404, never 403 -----------------------------------------


@pytest.mark.parametrize(
    "endpoint", [e for e in ALL_ENDPOINTS if e.takes_resource_id], ids=lambda e: e.name
)
async def test_cross_tenant_resource_returns_404(
    endpoint: ProtectedEndpoint,
    client: httpx.AsyncClient,
    fixtures: Fixtures,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
) -> None:
    """Section 7.2 / Section 24: never confirm a foreign resource exists."""
    actor = await fixtures.create_user(
        tenant_id=tenant,
        email=f"{endpoint.name}-x-actor@acme.example.com",
        roles=frozenset({sorted(endpoint.allowed_roles)[0]}),
    )
    foreign_target = await fixtures.create_user(
        tenant_id=other_tenant,
        email=f"{endpoint.name}-victim@globex.example.com",
        roles=frozenset({"client"}),
    )
    session_id = await fixtures.create_session(
        tenant_id=tenant, user_id=actor, mfa_verified_at=dt.datetime.now(dt.UTC)
    )

    response = await _call(client, endpoint, session_id=session_id, user_id=foreign_target)
    assert response.status_code == 404, (
        f"{endpoint.name}: cross-tenant id must answer 404, got {response.status_code}"
    )


@pytest.mark.parametrize(
    "endpoint", [e for e in ALL_ENDPOINTS if e.takes_resource_id], ids=lambda e: e.name
)
async def test_cross_tenant_and_nonexistent_are_indistinguishable(
    endpoint: ProtectedEndpoint,
    client: httpx.AsyncClient,
    fixtures: Fixtures,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
) -> None:
    """The two cases must produce byte-identical error codes."""
    actor = await fixtures.create_user(
        tenant_id=tenant,
        email=f"{endpoint.name}-cmp@acme.example.com",
        roles=frozenset({sorted(endpoint.allowed_roles)[0]}),
    )
    foreign = await fixtures.create_user(
        tenant_id=other_tenant,
        email=f"{endpoint.name}-cmp@globex.example.com",
        roles=frozenset({"client"}),
    )
    session_id = await fixtures.create_session(
        tenant_id=tenant, user_id=actor, mfa_verified_at=dt.datetime.now(dt.UTC)
    )

    foreign_response = await _call(client, endpoint, session_id=session_id, user_id=foreign)
    missing_response = await _call(client, endpoint, session_id=session_id, user_id=uuid.uuid4())

    assert foreign_response.status_code == missing_response.status_code == 404
    assert foreign_response.json()["error"]["code"] == missing_response.json()["error"]["code"]


# --- 3. Authenticated but without the permission -> 403 -------------------------


@pytest.mark.parametrize(
    "endpoint", [e for e in ALL_ENDPOINTS if e.denied_role], ids=lambda e: e.name
)
async def test_missing_permission_returns_403(
    endpoint: ProtectedEndpoint,
    client: httpx.AsyncClient,
    fixtures: Fixtures,
    tenant: uuid.UUID,
) -> None:
    actor = await fixtures.create_user(
        tenant_id=tenant,
        email=f"{endpoint.name}-denied@acme.example.com",
        roles=frozenset({endpoint.denied_role}),
    )
    target = await fixtures.create_user(
        tenant_id=tenant,
        email=f"{endpoint.name}-denied-target@acme.example.com",
        roles=frozenset({"client"}),
    )
    session_id = await fixtures.create_session(
        tenant_id=tenant, user_id=actor, mfa_verified_at=dt.datetime.now(dt.UTC)
    )

    response = await _call(client, endpoint, session_id=session_id, user_id=target)
    assert response.status_code == 403, (
        f"{endpoint.name}: {endpoint.denied_role} must be refused, got {response.status_code}"
    )


# --- 4. No session at all -> 401 -------------------------------------------------


@pytest.mark.parametrize("endpoint", ALL_ENDPOINTS, ids=lambda e: e.name)
async def test_unauthenticated_returns_401(
    endpoint: ProtectedEndpoint, client: httpx.AsyncClient
) -> None:
    path = endpoint.path.format(user_id=uuid.uuid4())
    response = await client.request(endpoint.method, path, json=endpoint.body)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


# --- 5. Step-up endpoints refuse a stale MFA verification (Section 7.3) ----------


@pytest.mark.parametrize("endpoint", [e for e in ALL_ENDPOINTS if e.step_up], ids=lambda e: e.name)
async def test_step_up_endpoint_refuses_stale_mfa(
    endpoint: ProtectedEndpoint,
    client: httpx.AsyncClient,
    fixtures: Fixtures,
    tenant: uuid.UUID,
) -> None:
    """Performed with a stale step-up token: expect 403. Then fresh: expect success."""
    actor = await fixtures.create_user(
        tenant_id=tenant,
        email=f"{endpoint.name}-stale@acme.example.com",
        roles=frozenset({sorted(endpoint.allowed_roles)[0]}),
    )
    await fixtures.create_user(
        tenant_id=tenant,
        email=f"{endpoint.name}-stale-spare@acme.example.com",
        roles=frozenset({"org_admin"}),
    )
    target = await fixtures.create_user(
        tenant_id=tenant,
        email=f"{endpoint.name}-stale-target@acme.example.com",
        roles=frozenset({"client"}),
    )

    stale = await fixtures.create_session(
        tenant_id=tenant,
        user_id=actor,
        mfa_verified_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=6),
    )
    stale_response = await _call(client, endpoint, session_id=stale, user_id=target)
    assert stale_response.status_code == 403
    assert stale_response.json()["error"]["code"] == "STEP_UP_REQUIRED"
    assert stale_response.json()["error"]["details"] == {"method": "any"}

    fresh = await fixtures.create_session(
        tenant_id=tenant, user_id=actor, mfa_verified_at=dt.datetime.now(dt.UTC)
    )
    fresh_response = await _call(client, endpoint, session_id=fresh, user_id=target)
    assert fresh_response.status_code in endpoint.expected_success


@pytest.mark.parametrize("endpoint", [e for e in ALL_ENDPOINTS if e.step_up], ids=lambda e: e.name)
async def test_step_up_endpoint_refuses_session_that_never_did_mfa(
    endpoint: ProtectedEndpoint,
    client: httpx.AsyncClient,
    fixtures: Fixtures,
    tenant: uuid.UUID,
) -> None:
    actor = await fixtures.create_user(
        tenant_id=tenant,
        email=f"{endpoint.name}-nomfa@acme.example.com",
        roles=frozenset({sorted(endpoint.allowed_roles)[0]}),
    )
    target = await fixtures.create_user(
        tenant_id=tenant,
        email=f"{endpoint.name}-nomfa-target@acme.example.com",
        roles=frozenset({"client"}),
    )
    session_id = await fixtures.create_session(
        tenant_id=tenant, user_id=actor, mfa_verified_at=None
    )
    response = await _call(client, endpoint, session_id=session_id, user_id=target)
    assert response.status_code == 403
