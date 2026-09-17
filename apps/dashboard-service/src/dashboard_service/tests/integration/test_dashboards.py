"""Dashboards and tiles (Sections 9, 16, 32 Step D): visibility, ownership, pin, tile changes."""

from __future__ import annotations

import uuid

import pytest

from dashboard_service.tests.conftest import Harness

pytestmark = [pytest.mark.integration, pytest.mark.security]


async def test_client_creates_a_dashboard_and_pins_an_artifact(
    harness: Harness, tenant: uuid.UUID
) -> None:
    """Section 32 Step D for a `client`-role user."""
    who = harness.identity.add_user(tenant, {"client"})
    artifact_id = await harness.store_artifact(tenant)
    dashboard_id = await harness.dashboard(who)
    pinned = await harness.client.post(
        f"/api/v1/dashboards/{dashboard_id}/tiles",
        json={"artifact_id": artifact_id},
        headers=who.headers,
    )
    assert pinned.status_code == 201, pinned.text
    tile = pinned.json()
    assert tile["dashboard_id"] == dashboard_id and tile["artifact_id"] == artifact_id
    assert tile["chart_spec_version"] == 1 and tile["overrides"] == {}
    assert tile["position"] == {"x": 0, "y": 0, "w": 6, "h": 4}

    second = await harness.client.post(
        f"/api/v1/dashboards/{dashboard_id}/tiles",
        json={"artifact_id": artifact_id},
        headers=who.headers,
    )
    assert second.json()["position"]["y"] == 4

    detail = await harness.client.get(f"/api/v1/dashboards/{dashboard_id}", headers=who.headers)
    assert detail.status_code == 200
    assert [t["id"] for t in detail.json()["tiles"]] == [tile["id"], second.json()["id"]]
    assert detail.json()["is_owner"] is True

    assert [str(e.tile_id) for e in harness.events.pinned] == [tile["id"], second.json()["id"]]
    event = harness.events.pinned[0]
    assert (str(event.tenant_id), str(event.user_id)) == (str(tenant), str(who.user_id))


async def test_pin_survives_an_event_stream_outage(harness: Harness, tenant: uuid.UUID) -> None:
    who = harness.identity.add_user(tenant, {"client"})
    artifact_id = await harness.store_artifact(tenant)
    dashboard_id = await harness.dashboard(who)
    harness.events.fail = True
    pinned = await harness.client.post(
        f"/api/v1/dashboards/{dashboard_id}/tiles",
        json={"artifact_id": artifact_id},
        headers=who.headers,
    )
    assert pinned.status_code == 201
    detail = await harness.client.get(f"/api/v1/dashboards/{dashboard_id}", headers=who.headers)
    assert len(detail.json()["tiles"]) == 1


async def test_permissions_for_create_and_pin(harness: Harness, tenant: uuid.UUID) -> None:
    auditor = harness.identity.add_user(tenant, {"auditor"})
    denied = await harness.client.post(
        "/api/v1/dashboards", json={"name": "x"}, headers=auditor.headers
    )
    assert denied.status_code == 403
    assert (
        await harness.client.get("/api/v1/dashboards", headers=auditor.headers)
    ).status_code == 200
    billing = harness.identity.add_user(tenant, {"billing_admin"})
    assert (
        await harness.client.get("/api/v1/dashboards", headers=billing.headers)
    ).status_code == 403
    who = harness.identity.add_user(tenant, {"client"})
    link = await harness.client.post(
        "/api/v1/dashboards", json={"name": "x", "visibility": "link"}, headers=who.headers
    )
    assert link.status_code == 422
    markup = await harness.client.post(
        "/api/v1/dashboards", json={"name": "<script>"}, headers=who.headers
    )
    assert markup.status_code == 422


async def test_visibility_and_ownership(harness: Harness, tenant: uuid.UUID) -> None:
    owner = harness.identity.add_user(tenant, {"client"})
    colleague = harness.identity.add_user(tenant, {"developer"})
    artifact_id = await harness.store_artifact(tenant)
    private_id = await harness.dashboard(owner, "private")
    shared_id = await harness.dashboard(owner, "tenant")

    listed = await harness.client.get("/api/v1/dashboards", headers=colleague.headers)
    assert [d["id"] for d in listed.json()["items"]] == [shared_id]
    own = await harness.client.get("/api/v1/dashboards", headers=owner.headers)
    assert {d["id"] for d in own.json()["items"]} == {private_id, shared_id}

    hidden = await harness.client.get(f"/api/v1/dashboards/{private_id}", headers=colleague.headers)
    assert hidden.status_code == 404
    hidden_pin = await harness.client.post(
        f"/api/v1/dashboards/{private_id}/tiles",
        json={"artifact_id": artifact_id},
        headers=colleague.headers,
    )
    assert hidden_pin.status_code == 404

    readable = await harness.client.get(
        f"/api/v1/dashboards/{shared_id}", headers=colleague.headers
    )
    assert readable.status_code == 200 and readable.json()["is_owner"] is False
    not_owner = await harness.client.post(
        f"/api/v1/dashboards/{shared_id}/tiles",
        json={"artifact_id": artifact_id},
        headers=colleague.headers,
    )
    assert not_owner.status_code == 403
    assert not_owner.json()["error"]["code"] == "DASHBOARD_NOT_OWNER"


async def test_cross_tenant_ids_are_404(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    owner = harness.identity.add_user(tenant, {"client"})
    dashboard_id = await harness.dashboard(owner, "tenant")
    foreign_artifact = await harness.store_artifact(other_tenant)
    own_artifact = await harness.store_artifact(tenant)

    foreign_pin = await harness.client.post(
        f"/api/v1/dashboards/{dashboard_id}/tiles",
        json={"artifact_id": foreign_artifact},
        headers=owner.headers,
    )
    assert foreign_pin.status_code == 404

    stranger = harness.identity.add_user(other_tenant, {"org_admin"})
    assert (
        await harness.client.get(f"/api/v1/dashboards/{dashboard_id}", headers=stranger.headers)
    ).status_code == 404
    stranger_pin = await harness.client.post(
        f"/api/v1/dashboards/{dashboard_id}/tiles",
        json={"artifact_id": own_artifact},
        headers=stranger.headers,
    )
    assert stranger_pin.status_code == 404
    tile = await harness.client.post(
        f"/api/v1/dashboards/{dashboard_id}/tiles",
        json={"artifact_id": own_artifact},
        headers=owner.headers,
    )
    patched = await harness.client.patch(
        f"/api/v1/tiles/{tile.json()['id']}",
        json={"position": {"x": 0, "y": 0, "w": 12, "h": 4}},
        headers=stranger.headers,
    )
    assert patched.status_code == 404
    listed = await harness.client.get("/api/v1/dashboards", headers=stranger.headers)
    assert listed.json()["items"] == []


async def test_tile_layout_and_overrides(harness: Harness, tenant: uuid.UUID) -> None:
    owner = harness.identity.add_user(tenant, {"client"})
    colleague = harness.identity.add_user(tenant, {"client"})
    artifact_id = await harness.store_artifact(tenant)
    dashboard_id = await harness.dashboard(owner, "tenant")
    tile_id = (
        await harness.client.post(
            f"/api/v1/dashboards/{dashboard_id}/tiles",
            json={"artifact_id": artifact_id},
            headers=owner.headers,
        )
    ).json()["id"]
    url = f"/api/v1/tiles/{tile_id}"

    moved = await harness.client.patch(
        url, json={"position": {"x": 6, "y": 2, "w": 6, "h": 5}}, headers=owner.headers
    )
    assert moved.status_code == 200 and moved.json()["position"] == {"x": 6, "y": 2, "w": 6, "h": 5}
    for bad in (
        {"x": 8, "y": 0, "w": 6, "h": 4},
        {"x": 0, "y": 0, "w": 0, "h": 4},
        {"x": "1", "y": 0, "w": 6, "h": 4},
    ):
        rejected = await harness.client.patch(url, json={"position": bad}, headers=owner.headers)
        assert rejected.status_code == 422, bad

    titled = await harness.client.patch(
        url,
        json={"overrides": {"title": "Q2 revenue", "stacking": "stacked"}},
        headers=owner.headers,
    )
    assert titled.status_code == 200 and titled.json()["overrides"]["title"] == "Q2 revenue"
    for bad_overrides in ({"type": "pie"}, {"title": "<img src=x>"}, {"html": "<b>"}):
        rejected = await harness.client.patch(
            url, json={"overrides": bad_overrides}, headers=owner.headers
        )
        assert rejected.status_code == 422, bad_overrides
        assert rejected.json()["error"]["code"] == "CHART_SPEC_INVALID"

    assert (await harness.client.patch(url, json={}, headers=owner.headers)).status_code == 422
    not_owner = await harness.client.patch(
        url, json={"overrides": {"title": "mine"}}, headers=colleague.headers
    )
    assert not_owner.status_code == 403


async def test_dashboard_list_paginates(harness: Harness, tenant: uuid.UUID) -> None:
    owner = harness.identity.add_user(tenant, {"client"})
    created = {await harness.dashboard(owner) for _ in range(3)}
    first = await harness.client.get("/api/v1/dashboards?limit=2", headers=owner.headers)
    body = first.json()
    assert len(body["items"]) == 2 and body["next_cursor"]
    rest = await harness.client.get(
        f"/api/v1/dashboards?limit=2&cursor={body['next_cursor']}", headers=owner.headers
    )
    assert rest.json()["next_cursor"] is None
    assert {d["id"] for d in body["items"] + rest.json()["items"]} == created
    bad = await harness.client.get("/api/v1/dashboards?cursor=nope", headers=owner.headers)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_CURSOR"


async def test_health(harness: Harness) -> None:
    assert (await harness.client.get("/health/live")).status_code == 200
    ready = await harness.client.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["checks"]["database"] == "ok"
