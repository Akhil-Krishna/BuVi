"""`/me/notifications` (Section 9): only the caller's own in-app items; marking read is
owner-only, and another user's or tenant's id is `404`."""

from __future__ import annotations

import uuid

import pytest

from notification_service.tests.conftest import Caller, Harness
from platform_contracts import DashboardTilePinned

pytestmark = pytest.mark.integration


async def _pin(harness: Harness, who: Caller) -> None:
    await harness.dispatch(
        "dashboard.tile.pinned",
        DashboardTilePinned(
            tenant_id=who.tenant_id,
            dashboard_id=uuid.uuid4(),
            tile_id=uuid.uuid4(),
            artifact_id=uuid.uuid4(),
            user_id=who.user_id,
        ),
    )


async def test_inbox_pages_newest_first_and_marks_read(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    me = harness.identity.add_user(tenant, {"client"})
    colleague = harness.identity.add_user(tenant, {"client"})
    stranger = harness.identity.add_user(other_tenant, {"client"})
    for _ in range(3):
        await _pin(harness, me)
    await _pin(harness, colleague)
    await _pin(harness, stranger)

    first = (
        await harness.client.get(
            "/api/v1/me/notifications", params={"limit": 2}, headers=me.headers
        )
    ).json()
    assert len(first["items"]) == 2 and first["unread"] == 3 and first["next_cursor"]
    second = (
        await harness.client.get(
            "/api/v1/me/notifications",
            params={"limit": 2, "cursor": first["next_cursor"]},
            headers=me.headers,
        )
    ).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    ids = [i["id"] for i in first["items"] + second["items"]]
    assert len(set(ids)) == 3
    created = [i["created_at"] for i in first["items"] + second["items"]]
    assert created == sorted(created, reverse=True)

    read = await harness.client.post(f"/api/v1/me/notifications/{ids[0]}/read", headers=me.headers)
    assert read.status_code == 204
    again = await harness.client.post(f"/api/v1/me/notifications/{ids[0]}/read", headers=me.headers)
    assert again.status_code == 204  # idempotent
    unread = (
        await harness.client.get(
            "/api/v1/me/notifications", params={"unread_only": "true"}, headers=me.headers
        )
    ).json()
    assert ids[0] not in [i["id"] for i in unread["items"]] and unread["unread"] == 2
    listed = (await harness.client.get("/api/v1/me/notifications", headers=me.headers)).json()
    marked = next(i for i in listed["items"] if i["id"] == ids[0])
    assert marked["status"] == "read" and marked["read_at"] is not None

    # Someone else's notification -- same tenant or not -- is 404, and stays unread.
    theirs = (
        await harness.client.get("/api/v1/me/notifications", headers=colleague.headers)
    ).json()["items"][0]["id"]
    assert (
        await harness.client.post(f"/api/v1/me/notifications/{theirs}/read", headers=me.headers)
    ).status_code == 404
    foreign = (
        await harness.client.get("/api/v1/me/notifications", headers=stranger.headers)
    ).json()["items"][0]["id"]
    assert (
        await harness.client.post(f"/api/v1/me/notifications/{foreign}/read", headers=me.headers)
    ).status_code == 404
    assert (await harness.client.get("/api/v1/me/notifications", headers=colleague.headers)).json()[
        "unread"
    ] == 1


async def test_inbox_refusals(harness: Harness, tenant: uuid.UUID) -> None:
    me = harness.identity.add_user(tenant, {"client"})
    bad = await harness.client.get(
        "/api/v1/me/notifications", params={"cursor": "not-a-cursor"}, headers=me.headers
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_CURSOR"
    assert (await harness.client.get("/api/v1/me/notifications")).status_code == 401
    missing = await harness.client.post(
        f"/api/v1/me/notifications/{uuid.uuid4()}/read", headers=me.headers
    )
    assert missing.status_code == 404
