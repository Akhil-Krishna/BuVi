"""Artifacts: stored by runs (idempotent, validated), read by users (Sections 8.9, 9.1, 16)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from dashboard_service.tests.conftest import CHART_SPEC, Harness

pytestmark = [pytest.mark.integration, pytest.mark.security]

STORE = "/internal/v1/artifacts"


async def test_run_artifact_is_stored_once_and_replayed_for_the_same_run(
    harness: Harness, tenant: uuid.UUID, platform_db: Any
) -> None:
    body = harness.artifact_body(tenant)
    first = await harness.client.post(STORE, json=body, headers=harness.service_headers())
    assert first.status_code == 201, first.text
    assert first.json() == {"artifact_id": body["artifact_id"], "version": 1, "created": True}
    again = await harness.client.post(STORE, json=body, headers=harness.service_headers())
    assert again.status_code == 200 and again.json()["created"] is False
    other_run = await harness.client.post(
        STORE,
        json={**body, "run_id": str(uuid.uuid4())},
        headers=harness.service_headers(),
    )
    assert other_run.status_code == 409 and other_run.json()["error"]["code"] == "ARTIFACT_CONFLICT"
    count = await platform_db.fetchval(
        "SELECT count(*) FROM dashboard.artifacts WHERE id = $1", uuid.UUID(body["artifact_id"])
    )
    assert count == 1


async def test_invalid_chart_spec_is_rejected_and_nothing_is_stored(
    harness: Harness, tenant: uuid.UUID, platform_db: Any
) -> None:
    """Section 17: an unknown field is rejected, not silently dropped, before storage."""
    body = harness.artifact_body(tenant, chart_spec={**CHART_SPEC, "onRender": "alert(1)"})
    response = await harness.client.post(STORE, json=body, headers=harness.service_headers())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CHART_SPEC_INVALID"
    assert response.json()["error"]["details"]["problems"]
    stored = await platform_db.fetchval(
        "SELECT count(*) FROM dashboard.artifacts WHERE id = $1", uuid.UUID(body["artifact_id"])
    )
    assert stored == 0


async def test_visualization_outage_is_502_and_nothing_is_stored(
    harness: Harness, tenant: uuid.UUID
) -> None:
    harness.visualization.down = True
    response = await harness.client.post(
        STORE, json=harness.artifact_body(tenant), headers=harness.service_headers()
    )
    assert response.status_code == 502


async def test_only_the_orchestrator_with_the_scope_stores_artifacts(
    harness: Harness, tenant: uuid.UUID
) -> None:
    body = harness.artifact_body(tenant)
    assert (await harness.client.post(STORE, json=body)).status_code == 401
    wrong_scope = await harness.client.post(
        STORE, json=body, headers=harness.service_headers(scope="dashboard-service:proxy")
    )
    assert wrong_scope.status_code == 403
    wrong_caller = await harness.client.post(
        STORE, json=body, headers=harness.service_headers(subject="api-gateway")
    )
    assert wrong_caller.status_code == 403
    assert wrong_caller.json()["error"]["code"] == "ARTIFACT_WRITER_NOT_ALLOWED"
    smuggled = await harness.client.post(
        STORE, json={**body, "version": 7}, headers=harness.service_headers()
    )
    assert smuggled.status_code == 422


async def test_get_artifact_returns_the_section_9_1_shape(
    harness: Harness, tenant: uuid.UUID
) -> None:
    artifact_id = await harness.store_artifact(tenant)
    client_user = harness.identity.add_user(tenant, {"client"})
    response = await harness.client.get(
        f"/api/v1/artifacts/{artifact_id}", headers=client_user.headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "artifact_id",
        "title",
        "summary",
        "chart_spec",
        "result_schema",
        "source_refs",
        "refresh_policy",
        "version",
        "can_pin",
        "conversation_id",
        "run_id",
        "created_at",
    }
    assert body["chart_spec"]["type"] == "line" and body["refresh_policy"] == {"mode": "manual"}
    assert body["can_pin"] is True and body["summary"].startswith("Monthly revenue")
    auditor = harness.identity.add_user(tenant, {"auditor"})
    audited = await harness.client.get(f"/api/v1/artifacts/{artifact_id}", headers=auditor.headers)
    assert audited.status_code == 200 and audited.json()["can_pin"] is False


async def test_artifact_access_is_permission_and_tenant_checked(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    artifact_id = await harness.store_artifact(tenant)
    stranger = harness.identity.add_user(other_tenant, {"org_admin"})
    for path in (f"/api/v1/artifacts/{artifact_id}", f"/api/v1/artifacts/{artifact_id}/data"):
        assert (await harness.client.get(path, headers=stranger.headers)).status_code == 404
        assert (await harness.client.get(path)).status_code == 401
    billing = harness.identity.add_user(tenant, {"billing_admin"})
    denied = await harness.client.get(f"/api/v1/artifacts/{artifact_id}", headers=billing.headers)
    assert denied.status_code == 403
    missing = await harness.client.get(
        f"/api/v1/artifacts/{uuid.uuid4()}",
        headers=harness.identity.add_user(tenant, {"client"}).headers,
    )
    assert missing.status_code == 404


async def test_artifact_data_reads_the_stored_result_and_reports_expiry(
    harness: Harness, tenant: uuid.UUID
) -> None:
    handle = f"s3://query-results/tenants/{tenant}/queries/{uuid.uuid4()}.json"
    artifact_id = await harness.store_artifact(tenant, query_result_ref=handle)
    client_user = harness.identity.add_user(tenant, {"client"})
    response = await harness.client.get(
        f"/api/v1/artifacts/{artifact_id}/data", headers=client_user.headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["artifact_id"] == artifact_id and body["row_count"] == 2
    assert [c["name"] for c in body["columns"]] == ["month", "revenue"]
    assert harness.results.reads == [(tenant, handle)]

    harness.results.expired.add(handle)
    expired = await harness.client.get(
        f"/api/v1/artifacts/{artifact_id}/data", headers=client_user.headers
    )
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "ARTIFACT_RESULT_EXPIRED"


async def test_request_path_role_cannot_rewrite_artifacts(
    postgres: Any, harness: Harness, tenant: uuid.UUID
) -> None:
    """Section 16: an artifact change is a new version, never an in-place edit."""
    import asyncpg

    artifact_id = await harness.store_artifact(tenant)
    conn = await asyncpg.connect(postgres.app_dsn.replace("postgresql+asyncpg", "postgresql"))
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant))
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "UPDATE dashboard.artifacts SET title = 'x' WHERE id = $1",
                    uuid.UUID(artifact_id),
                )
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(uuid.uuid4()))
            assert await conn.fetchval("SELECT count(*) FROM dashboard.artifacts") == 0
    finally:
        await conn.close()
