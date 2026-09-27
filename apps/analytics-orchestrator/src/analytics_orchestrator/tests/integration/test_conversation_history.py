"""GET /conversations and GET /conversations/{id}/messages (chat history sidebar)."""

from __future__ import annotations

import uuid

import pytest

from analytics_orchestrator.tests.conftest import MESSAGE, Harness

pytestmark = pytest.mark.integration


async def test_a_user_sees_only_their_own_conversations_newest_first(
    harness: Harness, tenant: uuid.UUID
) -> None:
    mine = harness.services.add_user(tenant, {"client"})
    other = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)

    first = (
        await harness.client.post(
            "/api/v1/conversations", json={"title": "First"}, headers=mine.headers
        )
    ).json()["id"]
    second = (
        await harness.client.post(
            "/api/v1/conversations", json={"title": "Second"}, headers=mine.headers
        )
    ).json()["id"]
    await harness.client.post(
        "/api/v1/conversations", json={"title": "Not mine"}, headers=other.headers
    )

    response = await harness.client.get("/api/v1/conversations", headers=mine.headers)
    assert response.status_code == 200
    ids = [c["id"] for c in response.json()["items"]]
    assert ids == [second, first]  # newest first, other user's excluded


async def test_messages_of_a_conversation_come_back_in_order(
    harness: Harness, tenant: uuid.UUID
) -> None:
    who = harness.services.add_user(tenant, {"client"})
    harness.services.add_data_source(tenant)
    conv = await harness.conversation(who)
    run = await harness.client.post(
        f"/api/v1/conversations/{conv}/messages", json={"content": MESSAGE}, headers=who.headers
    )
    run_id = run.json()["run_id"]
    await harness.execute(who, run_id)

    response = await harness.client.get(
        f"/api/v1/conversations/{conv}/messages", headers=who.headers
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert [m["role"] for m in items] == ["user", "assistant"]
    assert items[0]["content"] == MESSAGE and items[0]["run_id"] == run_id


async def test_another_users_conversation_messages_are_a_404(
    harness: Harness, tenant: uuid.UUID
) -> None:
    owner = harness.services.add_user(tenant, {"client"})
    intruder = harness.services.add_user(tenant, {"client"})
    conv = await harness.conversation(owner)

    response = await harness.client.get(
        f"/api/v1/conversations/{conv}/messages", headers=intruder.headers
    )
    assert response.status_code == 404


async def test_another_tenants_conversation_is_also_a_404_not_a_403(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    theirs = harness.services.add_user(other_tenant, {"client"})
    conv = await harness.conversation(theirs)
    ours = harness.services.add_user(tenant, {"client"})

    response = await harness.client.get(
        f"/api/v1/conversations/{conv}/messages", headers=ours.headers
    )
    assert response.status_code == 404
