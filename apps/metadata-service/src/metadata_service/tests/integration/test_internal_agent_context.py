"""Agent context endpoints (Sections 10.3, 12): what analytics-orchestrator may show an agent."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from metadata_service.tests.conftest import READER_PASSWORD, READER_USER, FakeIdentity, Flows
from platform_auth import ServiceTokenIssuer

pytestmark = [pytest.mark.integration, pytest.mark.security]


def _headers(issuer: ServiceTokenIssuer, scope: str = "metadata-service:context") -> dict[str, str]:
    token = issuer.issue(
        subject="analytics-orchestrator", audience="metadata-service", scopes=frozenset({scope})
    )
    return {"X-Service-Authorization": f"Bearer {token}"}


async def test_context_hides_pii_hidden_tables_and_credentials(
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
        "UPDATE metadata.tables SET is_visible_to_agent = false WHERE data_source_id = $1 AND table_name = 'order_items'",
        source_id,
    )

    response = await client.get(
        f"/internal/v1/data-sources/{source_id}/context",
        params={"tenant_id": str(tenant)},
        headers=_headers(gateway_issuer),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    tables = {t["table_name"]: t for t in body["tables"]}
    assert "order_items" not in tables and "orders" in tables
    assert "email" not in {c["column_name"] for c in tables["customers"]["columns"]}
    assert {c["column_name"] for c in tables["orders"]["columns"]} == {
        "id",
        "customer_id",
        "order_date",
        "amount",
    }
    assert body["status"] == "active" and body["engine"] == "postgres"
    for leak in ("secret_ref", "secret/data", READER_PASSWORD, READER_USER):
        assert leak not in response.text

    listed = await client.get(
        "/internal/v1/data-sources",
        params={"tenant_id": str(tenant)},
        headers=_headers(gateway_issuer),
    )
    assert [item["id"] for item in listed.json()["items"]] == [str(source_id)]


async def test_context_refusals(
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
    gateway_issuer: ServiceTokenIssuer,
) -> None:
    source = await flows.create(identity.add(tenant_id=tenant, roles={"org_admin"}))
    url = f"/internal/v1/data-sources/{source['id']}/context"
    assert (await client.get(url, params={"tenant_id": str(tenant)})).status_code == 401
    wrong = await client.get(
        url,
        params={"tenant_id": str(tenant)},
        headers=_headers(gateway_issuer, "metadata-service:query-policy"),
    )
    assert wrong.status_code == 403
    foreign = await client.get(
        url, params={"tenant_id": str(other_tenant)}, headers=_headers(gateway_issuer)
    )
    assert foreign.status_code == 404
    listed = await client.get(
        "/internal/v1/data-sources",
        params={"tenant_id": str(tenant)},
        headers=_headers(gateway_issuer),
    )
    assert listed.json()["items"] == []  # created but not active


async def test_context_carries_ids_and_catalog_lookup_resolves_them(
    client: httpx.AsyncClient,
    identity: FakeIdentity,
    flows: Flows,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
    gateway_issuer: ServiceTokenIssuer,
    platform_db: Any,
) -> None:
    """Phase A7: semantic definitions reference catalog ids; the agent packet exposes them."""
    source = await flows.synced(identity.add(tenant_id=tenant, roles={"org_admin"}))
    source_id = uuid.UUID(source["id"])
    await platform_db.execute(
        "UPDATE metadata.columns c SET is_pii = true FROM metadata.tables t "
        "WHERE c.table_id = t.id AND t.data_source_id = $1 AND c.column_name = 'email'",
        source_id,
    )
    context = (
        await client.get(
            f"/internal/v1/data-sources/{source_id}/context",
            params={"tenant_id": str(tenant)},
            headers=_headers(gateway_issuer),
        )
    ).json()
    orders = next(t for t in context["tables"] if t["table_name"] == "orders")
    amount = next(c for c in orders["columns"] if c["column_name"] == "amount")
    assert uuid.UUID(orders["id"]) and uuid.UUID(amount["id"])
    email_id = await platform_db.fetchval(
        "SELECT c.id FROM metadata.columns c JOIN metadata.tables t ON t.id = c.table_id "
        "WHERE t.data_source_id = $1 AND c.column_name = 'email'",
        source_id,
    )

    lookup = "/internal/v1/catalog/lookup"
    headers = _headers(gateway_issuer, "metadata-service:catalog-lookup")
    body = {
        "tenant_id": str(tenant),
        "table_ids": [orders["id"], str(uuid.uuid4())],
        "column_ids": [amount["id"], str(email_id)],
    }
    found = await client.post(lookup, json=body, headers=headers)
    assert found.status_code == 200, found.text
    result = found.json()
    assert [t["id"] for t in result["tables"]] == [orders["id"]]
    table = result["tables"][0]
    assert table["data_source_id"] == str(source_id) and table["is_visible_to_agent"] is True
    assert {c["column_name"] for c in table["columns"]} >= {"amount", "order_date"}
    flags = {c["column_name"]: c["is_pii"] for c in result["columns"]}
    assert flags == {"amount": False, "email": True}

    foreign = await client.post(
        lookup, json={**body, "tenant_id": str(other_tenant)}, headers=headers
    )
    assert foreign.json() == {"tables": [], "columns": []}
    wrong_scope = await client.post(lookup, json=body, headers=_headers(gateway_issuer))
    assert wrong_scope.status_code == 403
    assert (await client.post(lookup, json=body)).status_code == 401
