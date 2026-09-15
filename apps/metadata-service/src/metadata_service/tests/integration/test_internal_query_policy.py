"""`GET /internal/v1/data-sources/{id}/query-policy`: what query-gateway loads before a query."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from metadata_service.tests.conftest import (
    CUSTOMER_DB,
    EXPECTED_TABLES,
    READER_PASSWORD,
    READER_USER,
    FakeIdentity,
    Flows,
)
from platform_auth import ServiceTokenIssuer

pytestmark = [pytest.mark.integration, pytest.mark.security]

SCOPE = "metadata-service:query-policy"


def _headers(issuer: ServiceTokenIssuer, scope: str = SCOPE) -> dict[str, str]:
    token = issuer.issue(
        subject="query-gateway", audience="metadata-service", scopes=frozenset({scope})
    )
    return {"X-Service-Authorization": f"Bearer {token}"}


async def test_policy_returns_catalog_flags_and_the_vault_pointer_only(
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    tenant: uuid.UUID,
    gateway_issuer: ServiceTokenIssuer,
    platform_db: Any,
) -> None:
    source = await flows.synced(identity.add(tenant_id=tenant, roles={"org_admin"}))
    source_id = uuid.UUID(source["id"])
    await platform_db.execute(
        "UPDATE metadata.columns c SET is_pii = true FROM metadata.tables t "
        "WHERE c.table_id = t.id AND t.data_source_id = $1 AND c.column_name = 'email'",
        source_id,
    )
    await platform_db.execute(
        "UPDATE metadata.tables SET is_visible_to_agent = false "
        "WHERE data_source_id = $1 AND table_name = 'regions'",
        source_id,
    )

    response = await client.get(
        f"/internal/v1/data-sources/{source_id}/query-policy",
        params={"tenant_id": str(tenant)},
        headers=_headers(gateway_issuer),
    )
    assert response.status_code == 200, response.text
    policy = response.json()
    assert policy["engine"] == "postgres"
    assert policy["database_name"] == CUSTOMER_DB
    assert policy["allowed_schemas"] == ["sales"]
    assert policy["status"] == "active"
    assert policy["secret_ref"] == f"secret/data/tenants/{tenant}/datasources/{source_id}"
    tables = {t["table_name"]: t for t in policy["tables"]}
    assert sorted(tables) == EXPECTED_TABLES
    assert tables["regions"]["is_visible_to_agent"] is False
    emails = {c["column_name"]: c["is_pii"] for c in tables["customers"]["columns"]}
    assert emails["email"] is True and emails["name"] is False
    assert READER_PASSWORD not in response.text and READER_USER not in response.text


async def test_policy_refusals(
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
    gateway_issuer: ServiceTokenIssuer,
) -> None:
    source = await flows.create(identity.add(tenant_id=tenant, roles={"org_admin"}))
    url = f"/internal/v1/data-sources/{source['id']}/query-policy"
    ours = {"tenant_id": str(tenant)}

    no_token = await client.get(url, params=ours)
    assert no_token.status_code == 401
    wrong_scope = await client.get(
        url, params=ours, headers=_headers(gateway_issuer, "metadata-service:proxy")
    )
    assert wrong_scope.status_code == 403
    missing_tenant = await client.get(url, headers=_headers(gateway_issuer))
    assert missing_tenant.status_code == 422

    foreign = await client.get(
        url, params={"tenant_id": str(other_tenant)}, headers=_headers(gateway_issuer)
    )
    missing = await client.get(
        f"/internal/v1/data-sources/{uuid.uuid4()}/query-policy",
        params=ours,
        headers=_headers(gateway_issuer),
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["code"] == missing.json()["error"]["code"] == "NOT_FOUND"


async def test_policy_route_is_not_reachable_with_a_user_credential_alone(
    client: httpx.AsyncClient, identity: FakeIdentity, flows: Flows, tenant: uuid.UUID
) -> None:
    who = identity.add(tenant_id=tenant, roles={"org_admin"})
    source = await flows.create(who)
    response = await client.get(
        f"/internal/v1/data-sources/{source['id']}/query-policy",
        params={"tenant_id": str(tenant)},
        headers=who.headers,
    )
    assert response.status_code == 401
