"""Semantic definitions over HTTP (Phase A7 DoD: `GET/POST /semantic/metrics` fully testable)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from semantic_service.tests.conftest import Harness

pytestmark = [pytest.mark.integration, pytest.mark.security]

METRICS = "/api/v1/semantic/metrics"


async def test_create_list_get_approve_and_deprecate_a_metric(
    harness: Harness, tenant: uuid.UUID
) -> None:
    developer = harness.identity.add_user(tenant, {"developer"})
    orders = harness.catalog.add_orders(tenant)
    created = await harness.metric(
        developer,
        orders,
        expression="sum( orders.amount )",
        description="Completed order value in USD",
        synonyms=["Sales", "turnover", "sales"],
        default_grain="month",
    )
    assert created["status"] == "draft" and created["expression"] == "SUM(amount)"
    assert created["synonyms"] == ["sales", "turnover"]
    assert created["created_by"] == str(developer.user_id) and created["approved_by"] is None

    listed = await harness.client.get(METRICS, headers=developer.headers)
    assert [m["id"] for m in listed.json()["items"]] == [created["id"]]
    drafts = await harness.client.get(
        METRICS, params={"status": "approved"}, headers=developer.headers
    )
    assert drafts.json()["items"] == []

    admin = harness.identity.add_user(tenant, {"org_admin"})
    approved = await harness.client.post(
        f"{METRICS}/{created['id']}/approve", headers=admin.headers
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"] == str(admin.user_id) and approved.json()["approved_at"]
    again = await harness.client.post(f"{METRICS}/{created['id']}/approve", headers=admin.headers)
    assert again.status_code == 409 and again.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"

    fetched = await harness.client.get(f"{METRICS}/{created['id']}", headers=developer.headers)
    assert fetched.json()["status"] == "approved"
    deprecated = await harness.client.post(
        f"{METRICS}/{created['id']}/deprecate", headers=admin.headers
    )
    assert deprecated.json()["status"] == "deprecated"

    events = [(e.event_type, str(e.actor_user_id)) for e in harness.audit.events]
    assert events == [
        ("semantic.metric.created", str(developer.user_id)),
        ("semantic.metric.approved", str(admin.user_id)),
        ("semantic.metric.deprecated", str(admin.user_id)),
    ]
    assert harness.audit.events[1].before_state["status"] == "draft"  # type: ignore[index]


@pytest.mark.parametrize(
    ("expression", "column_flag", "problem"),
    [
        ("SUM(amount) + 1", None, "expression: expression must be AGG([DISTINCT] column)"),
        ("COUNT(email)", None, "expression: column is PII"),
        ("SUM(status)", None, "expression: SUM/AVG need a numeric column"),
        ("SUM(profit)", None, "expression: column not in the base table"),
        ("SUM(amount)", "hidden", "base_table_id: table is hidden from agents"),
    ],
)
async def test_invalid_definitions_are_422_with_reasons(
    harness: Harness, tenant: uuid.UUID, expression: str, column_flag: str | None, problem: str
) -> None:
    developer = harness.identity.add_user(tenant, {"developer"})
    orders = harness.catalog.add_orders(tenant, visible=column_flag != "hidden")
    response = await harness.client.post(
        METRICS,
        json={"name": "Revenue", "expression": expression, "base_table_id": str(orders.id)},
        headers=developer.headers,
    )
    assert response.status_code == 422, response.text
    body = response.json()["error"]
    assert body["code"] == "SEMANTIC_DEFINITION_INVALID" and problem in body["details"]["problems"]


async def test_another_tenants_table_is_not_in_the_catalog(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    developer = harness.identity.add_user(tenant, {"developer"})
    foreign = harness.catalog.add_orders(other_tenant)
    response = await harness.client.post(
        METRICS,
        json={"name": "Revenue", "expression": "SUM(amount)", "base_table_id": str(foreign.id)},
        headers=developer.headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["problems"] == ["base_table_id: not in the catalog"]


async def test_names_are_unique_per_tenant(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    developer = harness.identity.add_user(tenant, {"developer"})
    orders = harness.catalog.add_orders(tenant)
    await harness.metric(developer, orders)
    duplicate = await harness.client.post(
        METRICS,
        json={"name": "Revenue", "expression": "SUM(amount)", "base_table_id": str(orders.id)},
        headers=developer.headers,
    )
    assert (
        duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "SEMANTIC_NAME_TAKEN"
    )
    other = harness.identity.add_user(other_tenant, {"developer"})
    await harness.metric(other, harness.catalog.add_orders(other_tenant))


async def test_approval_rechecks_the_catalog(harness: Harness, tenant: uuid.UUID) -> None:
    developer = harness.identity.add_user(tenant, {"developer"})
    orders = harness.catalog.add_orders(tenant)
    created = await harness.metric(developer, orders)
    column_id, data_type, _ = orders.columns["amount"]
    orders.columns["amount"] = (column_id, data_type, True)  # re-classified as PII since the draft
    response = await harness.client.post(
        f"{METRICS}/{created['id']}/approve", headers=developer.headers
    )
    assert response.status_code == 422
    assert "expression: column is PII" in response.json()["error"]["details"]["problems"]


async def test_authorization_triplet_and_catalog_outage(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    developer = harness.identity.add_user(tenant, {"developer"})
    created = await harness.metric(developer, harness.catalog.add_orders(tenant))
    stranger = harness.identity.add_user(other_tenant, {"org_admin"})
    for path in (f"{METRICS}/{created['id']}", f"{METRICS}/{created['id']}/approve"):
        method = harness.client.get if path.endswith(created["id"]) else harness.client.post
        assert (await method(path, headers=stranger.headers)).status_code == 404
    client_user = harness.identity.add_user(tenant, {"client"})
    assert (await harness.client.get(METRICS, headers=client_user.headers)).status_code == 403
    assert (await harness.client.get(METRICS)).status_code == 401
    other_list = await harness.client.get(METRICS, headers=stranger.headers)
    assert other_list.json()["items"] == []

    harness.catalog.down = True
    outage = await harness.client.post(
        METRICS,
        json={"name": "Orders", "expression": "COUNT(id)", "base_table_id": str(uuid.uuid4())},
        headers=developer.headers,
    )
    assert outage.status_code == 502


async def test_list_paginates_and_rejects_bad_cursors(harness: Harness, tenant: uuid.UUID) -> None:
    developer = harness.identity.add_user(tenant, {"developer"})
    orders = harness.catalog.add_orders(tenant)
    names = {"Revenue", "Orders", "Average order"}
    for name, expression in zip(
        sorted(names), ("AVG(amount)", "COUNT(id)", "SUM(amount)"), strict=True
    ):
        await harness.metric(developer, orders, name=name, expression=expression)
    first = (
        await harness.client.get(METRICS, params={"limit": 2}, headers=developer.headers)
    ).json()
    rest = (
        await harness.client.get(
            METRICS, params={"limit": 2, "cursor": first["next_cursor"]}, headers=developer.headers
        )
    ).json()
    assert {m["name"] for m in first["items"] + rest["items"]} == names and rest[
        "next_cursor"
    ] is None
    bad = await harness.client.get(METRICS, params={"cursor": "x"}, headers=developer.headers)
    assert bad.status_code == 422


async def test_dimensions(harness: Harness, tenant: uuid.UUID) -> None:
    developer = harness.identity.add_user(tenant, {"developer"})
    orders = harness.catalog.add_orders(tenant)
    url = "/api/v1/semantic/dimensions"
    created = await harness.client.post(
        url,
        json={
            "name": "Order status",
            "column_id": str(harness.catalog.column_id(orders, "status")),
            "synonyms": ["State"],
        },
        headers=developer.headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["synonyms"] == ["state"]
    pii = await harness.client.post(
        url,
        json={"name": "Email", "column_id": str(harness.catalog.column_id(orders, "email"))},
        headers=developer.headers,
    )
    assert pii.status_code == 422
    unknown = await harness.client.post(
        url, json={"name": "Nope", "column_id": str(uuid.uuid4())}, headers=developer.headers
    )
    assert unknown.status_code == 422
    listed = await harness.client.get(url, headers=developer.headers)
    assert [d["name"] for d in listed.json()["items"]] == ["Order status"]


async def test_internal_context_serves_only_approved_metrics_parsed(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    developer = harness.identity.add_user(tenant, {"developer"})
    orders = harness.catalog.add_orders(tenant)
    revenue = await harness.metric(developer, orders, synonyms=["sales"])
    await harness.metric(developer, orders, name="Orders", expression="COUNT(DISTINCT id)")
    await harness.client.post(f"{METRICS}/{revenue['id']}/approve", headers=developer.headers)
    await harness.client.post(
        "/api/v1/semantic/dimensions",
        json={"name": "Status", "column_id": str(harness.catalog.column_id(orders, "status"))},
        headers=developer.headers,
    )
    url = "/internal/v1/semantic-context"
    response = await harness.client.get(
        url, params={"tenant_id": str(tenant)}, headers=harness.service_headers()
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert [(m["name"], m["aggregation"], m["column"]) for m in body["metrics"]] == [
        ("Revenue", "sum", "amount")
    ]
    assert body["metrics"][0]["base_table_id"] == str(orders.id)
    assert [d["name"] for d in body["dimensions"]] == ["Status"]

    foreign = await harness.client.get(
        url, params={"tenant_id": str(other_tenant)}, headers=harness.service_headers()
    )
    assert foreign.json()["metrics"] == [] and foreign.json()["dimensions"] == []
    assert (
        await harness.client.get(
            url,
            params={"tenant_id": str(tenant)},
            headers=harness.service_headers(scope="semantic-service:proxy"),
        )
    ).status_code == 403
    wrong_caller = await harness.client.get(
        url,
        params={"tenant_id": str(tenant)},
        headers=harness.service_headers(subject="api-gateway"),
    )
    assert wrong_caller.json()["error"]["code"] == "CONTEXT_READER_NOT_ALLOWED"


async def test_request_path_role_cannot_delete_definitions(
    postgres: Any, harness: Harness, tenant: uuid.UUID
) -> None:
    import asyncpg

    developer = harness.identity.add_user(tenant, {"developer"})
    created = await harness.metric(developer, harness.catalog.add_orders(tenant))
    conn = await asyncpg.connect(postgres.app_dsn.replace("postgresql+asyncpg", "postgresql"))
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant))
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "DELETE FROM semantic.metrics WHERE id = $1", uuid.UUID(created["id"])
                )
    finally:
        await conn.close()


async def test_health(harness: Harness) -> None:
    assert (await harness.client.get("/health/ready")).json()["checks"] == {"database": "ok"}
