"""Section 25 triplet for the chat routes, and service-scope checks on internal routes."""

from __future__ import annotations

import uuid

import pytest

from analytics_orchestrator.tests.conftest import MESSAGE, Harness

pytestmark = [pytest.mark.integration, pytest.mark.security]


@pytest.mark.parametrize("role", ["client", "developer", "org_admin"])
async def test_same_tenant_with_chat_use_is_allowed(
    harness: Harness, tenant: uuid.UUID, role: str
) -> None:
    who = harness.services.add_user(tenant, {role})
    run_id = await harness.start_run(who)
    assert (
        await harness.client.post(f"/api/v1/runs/{run_id}/cancel", headers=who.headers)
    ).status_code == 202


async def test_cross_tenant_ids_are_404_like_missing_ones(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    owner = harness.services.add_user(other_tenant, {"client"})
    conversation = await harness.conversation(owner)
    run_id = await harness.start_run(owner)
    intruder = harness.services.add_user(tenant, {"org_admin"})
    for path in (
        f"/api/v1/conversations/{conversation}/messages",
        f"/api/v1/conversations/{uuid.uuid4()}/messages",
    ):
        response = await harness.client.post(
            path, json={"content": MESSAGE}, headers=intruder.headers
        )
        assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"
    for path in (f"/api/v1/runs/{run_id}/cancel", f"/api/v1/runs/{uuid.uuid4()}/cancel"):
        assert (await harness.client.post(path, headers=intruder.headers)).status_code == 404
    events = await harness.client.get(
        f"/internal/v1/runs/{run_id}/events",
        params={"tenant_id": str(tenant)},
        headers=harness.service_headers("api-gateway", "analytics-orchestrator:events"),
    )
    assert events.status_code == 404
    execute = await harness.client.post(
        f"/internal/v1/runs/{run_id}/execute",
        params={"tenant_id": str(tenant)},
        headers=harness.service_headers("worker-runtime", "analytics-orchestrator:execute"),
    )
    assert execute.status_code == 404
    assert len(harness.queue.messages) == 1


@pytest.mark.parametrize("role", ["billing_admin"])
async def test_without_chat_use_is_403(harness: Harness, tenant: uuid.UUID, role: str) -> None:
    owner = harness.services.add_user(tenant, {"client"})
    conversation = await harness.conversation(owner)
    run_id = await harness.start_run(owner)
    who = harness.services.add_user(tenant, {role})
    assert (
        await harness.client.post("/api/v1/conversations", json={}, headers=who.headers)
    ).status_code == 403
    assert (
        await harness.client.post(
            f"/api/v1/conversations/{conversation}/messages",
            json={"content": MESSAGE},
            headers=who.headers,
        )
    ).status_code == 403
    assert (
        await harness.client.post(f"/api/v1/runs/{run_id}/cancel", headers=who.headers)
    ).status_code == 403


async def test_unauthenticated_is_401(harness: Harness) -> None:
    for method, path in (
        ("POST", "/api/v1/conversations"),
        ("POST", f"/api/v1/conversations/{uuid.uuid4()}/messages"),
        ("POST", f"/api/v1/runs/{uuid.uuid4()}/cancel"),
    ):
        response = await harness.client.request(method, path, json={"content": "x"})
        assert response.status_code == 401


async def test_internal_routes_require_their_own_scopes(
    harness: Harness, tenant: uuid.UUID
) -> None:
    owner = harness.services.add_user(tenant, {"client"})
    run_id = await harness.start_run(owner)
    execute = f"/internal/v1/runs/{run_id}/execute"
    events = f"/internal/v1/runs/{run_id}/events"
    params = {"tenant_id": str(tenant)}
    assert (await harness.client.post(execute, params=params)).status_code == 401
    assert (
        await harness.client.post(
            execute,
            params=params,
            headers=harness.service_headers("api-gateway", "analytics-orchestrator:events"),
        )
    ).status_code == 403
    assert (
        await harness.client.get(
            events,
            params=params,
            headers=harness.service_headers("worker-runtime", "analytics-orchestrator:execute"),
        )
    ).status_code == 403
    assert (
        await harness.client.post(execute, params=params, headers=owner.headers)
    ).status_code == 401


async def test_body_cannot_smuggle_run_fields(harness: Harness, tenant: uuid.UUID) -> None:
    who = harness.services.add_user(tenant, {"client"})
    conversation = await harness.conversation(who)
    for extra in (
        {"requested_by": str(uuid.uuid4())},
        {"flow_state": {}},
        {"tenant_id": str(uuid.uuid4())},
    ):
        response = await harness.client.post(
            f"/api/v1/conversations/{conversation}/messages",
            json={"content": MESSAGE, **extra},
            headers=who.headers,
        )
        assert response.status_code == 422
