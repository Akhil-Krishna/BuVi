"""Per-connection `sql:execute` grants (Section 7.1; Phase A10) over HTTP, and the uncached
check query-gateway makes before every SQL-editor query."""

from __future__ import annotations

import uuid

import httpx
import pytest

from metadata_service.tests.conftest import FakeIdentity, Flows
from platform_auth import ServiceTokenIssuer

pytestmark = [pytest.mark.integration, pytest.mark.security]


def _service(
    issuer: ServiceTokenIssuer, scope: str = "metadata-service:query-policy"
) -> dict[str, str]:
    token = issuer.issue(
        subject="query-gateway", audience="metadata-service", scopes=frozenset({scope})
    )
    return {"X-Service-Authorization": f"Bearer {token}"}


async def _check(
    client: httpx.AsyncClient,
    issuer: ServiceTokenIssuer,
    tenant: uuid.UUID,
    source: str,
    user: uuid.UUID,
) -> bool:
    response = await client.get(
        f"/internal/v1/data-sources/{source}/sql-grants/{user}",
        params={"tenant_id": str(tenant)},
        headers=_service(issuer),
    )
    assert response.status_code == 200, response.text
    granted: bool = response.json()["granted"]
    return granted


async def test_grant_list_check_and_revoke(
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    tenant: uuid.UUID,
    gateway_issuer: ServiceTokenIssuer,
) -> None:
    admin = identity.add(tenant_id=tenant, roles={"org_admin"})
    developer = identity.add(tenant_id=tenant, roles={"developer"})
    source = (await flows.create(admin))["id"]
    base = f"/api/v1/data-sources/{source}/sql-grants"
    assert not await _check(client, gateway_issuer, tenant, source, developer.user_id)

    granted = await client.post(
        base, json={"user_id": str(developer.user_id)}, headers=admin.headers
    )
    assert granted.status_code == 201, granted.text
    grant = granted.json()
    assert grant["user_id"] == str(developer.user_id) and grant["granted_by"] == str(admin.user_id)
    duplicate = await client.post(
        base, json={"user_id": str(developer.user_id)}, headers=admin.headers
    )
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "SQL_GRANT_EXISTS"
    listed = await client.get(base, headers=admin.headers)
    assert [g["id"] for g in listed.json()["items"]] == [grant["id"]]
    assert await _check(client, gateway_issuer, tenant, source, developer.user_id)

    revoked = await client.delete(f"{base}/{grant['id']}", headers=admin.headers)
    assert revoked.status_code == 204
    assert not await _check(client, gateway_issuer, tenant, source, developer.user_id)
    assert (await client.delete(f"{base}/{grant['id']}", headers=admin.headers)).status_code == 404
    events = [e["event_type"] for e in identity.audit_events]
    assert events[-2:] == ["connection.sql_grant_added", "connection.sql_grant_revoked"]


async def test_only_org_admins_of_the_same_tenant_manage_grants(
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
    gateway_issuer: ServiceTokenIssuer,
) -> None:
    admin = identity.add(tenant_id=tenant, roles={"org_admin"})
    source = (await flows.create(admin))["id"]
    base = f"/api/v1/data-sources/{source}/sql-grants"
    body = {"user_id": str(uuid.uuid4())}
    developer = identity.add(tenant_id=tenant, roles={"developer"})  # data:manage, not org_admin
    assert (await client.post(base, json=body, headers=developer.headers)).status_code == 403
    assert (await client.get(base, headers=developer.headers)).status_code == 403
    foreign = identity.add(tenant_id=other_tenant, roles={"org_admin"})
    assert (await client.post(base, json=body, headers=foreign.headers)).status_code == 404
    # The check is tenant-bound too: a grant is never visible from another tenant.
    await client.post(base, json=body, headers=admin.headers)
    assert not await _check(
        client, gateway_issuer, other_tenant, source, uuid.UUID(body["user_id"])
    )
    wrong_scope = await client.get(
        f"/internal/v1/data-sources/{source}/sql-grants/{body['user_id']}",
        params={"tenant_id": str(tenant)},
        headers=_service(gateway_issuer, "metadata-service:context"),
    )
    assert wrong_scope.status_code == 403
