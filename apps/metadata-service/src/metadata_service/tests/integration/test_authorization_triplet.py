"""The Section 25 authorization triplet for every metadata-service endpoint.

    "every endpoint that accepts a user-supplied identifier gets a same-tenant-allowed /
     cross-tenant-404 / no-permission-403 test triplet at minimum"

Endpoints are one table, so a route added without its authorization tests is a visible
omission. Cross-tenant ids answer 404, indistinguishable from ids that do not exist
(Section 7.2); step-up endpoints refuse stale and missing MFA (Section 7.3).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import pytest

from metadata_service.tests.conftest import Caller, FakeIdentity, Flows

pytestmark = [pytest.mark.integration, pytest.mark.security]

#: Section 7.1: who holds each permission these endpoints require.
ALLOWED_ROLES = {
    "data:manage": ("org_admin", "developer"),
    "catalog:read": ("org_admin", "developer", "auditor"),
}
DENIED_ROLES = {
    "data:manage": ("client", "auditor", "billing_admin"),
    "catalog:read": ("client", "billing_admin"),
}


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: str
    path: str
    permission: Literal["data:manage", "catalog:read"]
    body: Literal["create", "secret"] | None = None
    step_up: bool = False

    @property
    def takes_id(self) -> bool:
        return "{ds}" in self.path

    def url(self, ds: str, table: str) -> str:
        return self.path.format(ds=ds, table=table)


MANAGE, READ = "data:manage", "catalog:read"
ENDPOINTS = [
    Endpoint("list_data_sources", "GET", "/api/v1/data-sources", MANAGE),
    Endpoint("create_data_source", "POST", "/api/v1/data-sources", MANAGE, body="create"),
    Endpoint("read_data_source", "GET", "/api/v1/data-sources/{ds}", READ),
    Endpoint(
        "set_secret",
        "POST",
        "/api/v1/data-sources/{ds}/secret",
        MANAGE,
        body="secret",
        step_up=True,
    ),
    Endpoint("test_connection", "POST", "/api/v1/data-sources/{ds}/test", MANAGE),
    Endpoint("sync_catalog", "POST", "/api/v1/data-sources/{ds}/sync", MANAGE),
    Endpoint("list_tables", "GET", "/api/v1/data-sources/{ds}/tables", READ),
    Endpoint("read_table", "GET", "/api/v1/data-sources/{ds}/tables/{table}", READ),
]
ALLOWED_CASES = [(e, role) for e in ENDPOINTS for role in ALLOWED_ROLES[e.permission]]
DENIED_CASES = [(e, role) for e in ENDPOINTS for role in DENIED_ROLES[e.permission]]


def _case_id(case: object) -> str:
    return case.name if isinstance(case, Endpoint) else str(case)


WITH_ID = [e for e in ENDPOINTS if e.takes_id]


@dataclass(frozen=True)
class Seeded:
    ours: dict[str, Any]
    theirs: dict[str, Any]


@pytest.fixture
async def seeded(
    identity: FakeIdentity, flows: Flows, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> Seeded:
    ours = await flows.synced(identity.add(tenant_id=tenant, roles={"org_admin"}))
    theirs = await flows.synced(identity.add(tenant_id=other_tenant, roles={"org_admin"}))
    return Seeded(ours=ours, theirs=theirs)


async def _call(
    client: httpx.AsyncClient,
    flows: Flows,
    endpoint: Endpoint,
    who: Caller | None,
    *,
    ds: str,
    table: str,
) -> httpx.Response:
    body = None
    if endpoint.body == "create":
        body = flows.create_body()
    elif endpoint.body == "secret":
        body = flows.secret_body()
    return await client.request(
        endpoint.method,
        endpoint.url(ds, table),
        json=body,
        headers=who.headers if who else None,
    )


# --- 1. Same tenant, holding the endpoint's permission -> allowed ---------------------------------------


@pytest.mark.parametrize(("endpoint", "role"), ALLOWED_CASES, ids=_case_id)
async def test_same_tenant_with_permission_is_allowed(
    endpoint: Endpoint,
    role: str,
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    tenant: uuid.UUID,
    seeded: Seeded,
) -> None:
    who = identity.add(tenant_id=tenant, roles={role})
    response = await _call(
        client, flows, endpoint, who, ds=seeded.ours["id"], table=seeded.ours["table_id"]
    )
    assert response.status_code in (200, 201), (
        f"{endpoint.name}: {response.status_code} {response.text}"
    )


# --- 2. Cross tenant -> 404, indistinguishable from nonexistent ---------------------------------


@pytest.mark.parametrize("endpoint", WITH_ID, ids=lambda e: e.name)
async def test_cross_tenant_resource_is_404_like_a_missing_one(
    endpoint: Endpoint,
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    tenant: uuid.UUID,
    seeded: Seeded,
) -> None:
    who = identity.add(tenant_id=tenant, roles={"org_admin"})
    foreign = await _call(
        client, flows, endpoint, who, ds=seeded.theirs["id"], table=seeded.theirs["table_id"]
    )
    missing = await _call(
        client, flows, endpoint, who, ds=str(uuid.uuid4()), table=str(uuid.uuid4())
    )
    assert foreign.status_code == missing.status_code == 404, (foreign.text, missing.text)
    assert foreign.json()["error"]["code"] == missing.json()["error"]["code"] == "NOT_FOUND"


async def test_a_foreign_table_under_our_data_source_is_404(
    client: httpx.AsyncClient, identity: FakeIdentity, tenant: uuid.UUID, seeded: Seeded
) -> None:
    who = identity.add(tenant_id=tenant, roles={"org_admin"})
    response = await client.get(
        f"/api/v1/data-sources/{seeded.ours['id']}/tables/{seeded.theirs['table_id']}",
        headers=who.headers,
    )
    assert response.status_code == 404


async def test_cross_tenant_writes_change_nothing(
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
    seeded: Seeded,
    platform_db: Any,
) -> None:
    who = identity.add(tenant_id=tenant, roles={"org_admin"})
    before = await platform_db.fetchrow(
        "SELECT status, last_sync_at, updated_at FROM metadata.data_sources WHERE id = $1",
        uuid.UUID(seeded.theirs["id"]),
    )
    for action in ("secret", "test", "sync"):
        endpoint = next(e for e in WITH_ID if e.path.endswith(action))
        await _call(client, flows, endpoint, who, ds=seeded.theirs["id"], table="x")
    after = await platform_db.fetchrow(
        "SELECT status, last_sync_at, updated_at FROM metadata.data_sources WHERE id = $1",
        uuid.UUID(seeded.theirs["id"]),
    )
    assert dict(before) == dict(after)
    ours = await client.get("/api/v1/data-sources", headers=who.headers)
    assert seeded.theirs["id"] not in ours.text


# --- 3. Authenticated without the endpoint's permission -> 403 -------------------------------------------------


@pytest.mark.parametrize(("endpoint", "role"), DENIED_CASES, ids=_case_id)
async def test_missing_permission_is_403(
    endpoint: Endpoint,
    role: str,
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    tenant: uuid.UUID,
    seeded: Seeded,
) -> None:
    who = identity.add(tenant_id=tenant, roles={role})
    response = await _call(
        client, flows, endpoint, who, ds=seeded.ours["id"], table=seeded.ours["table_id"]
    )
    assert response.status_code == 403, f"{endpoint.name}/{role}: {response.status_code}"
    assert response.json()["error"]["code"] == "FORBIDDEN"


# --- 4. No credential -> 401 -------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=lambda e: e.name)
async def test_unauthenticated_is_401(
    endpoint: Endpoint, client: httpx.AsyncClient, flows: Flows
) -> None:
    response = await _call(
        client, flows, endpoint, None, ds=str(uuid.uuid4()), table=str(uuid.uuid4())
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


# --- 5. Step-up (Section 7.3) --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mfa_age", [dt.timedelta(minutes=6), None], ids=["stale-mfa", "never-verified"]
)
async def test_setting_credentials_requires_fresh_step_up(
    mfa_age: dt.timedelta | None,
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    secrets: Any,
    tenant: uuid.UUID,
    seeded: Seeded,
) -> None:
    ref = f"tenants/{tenant}/datasources/{seeded.ours['id']}"
    stored = await secrets.read(ref)
    who = identity.add(tenant_id=tenant, roles={"developer"}, mfa_age=mfa_age)
    response = await flows.set_secret(who, seeded.ours["id"], password="Replaced-Pa55word-01")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert "max_age=300" in response.headers["www-authenticate"]
    assert await secrets.read(ref) == stored
