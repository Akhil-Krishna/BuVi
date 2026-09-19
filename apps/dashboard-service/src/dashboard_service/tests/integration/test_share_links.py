"""Share links and the guest snapshot, and the export threshold (Phase A10), over HTTP."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from dashboard_service.tests.conftest import Caller, Harness

pytestmark = [pytest.mark.integration, pytest.mark.security]


async def _pinned_dashboard(
    harness: Harness, tenant: uuid.UUID, *, visibility: str = "private"
) -> tuple[Caller, str, str]:
    owner = harness.identity.add_user(tenant, {"developer"}, fresh_mfa=True)
    artifact = await harness.store_artifact(tenant)
    dashboard = await harness.dashboard(owner, visibility)
    pinned = await harness.client.post(
        f"/api/v1/dashboards/{dashboard}/tiles",
        json={"artifact_id": artifact},
        headers=owner.headers,
    )
    assert pinned.status_code == 201, pinned.text
    return owner, dashboard, artifact


async def _share(harness: Harness, who: Caller, dashboard: str, **body: Any) -> Any:
    return await harness.client.post(
        f"/api/v1/dashboards/{dashboard}/share-links", json=body or None, headers=who.headers
    )


async def test_owner_shares_and_a_guest_sees_only_the_charts(
    harness: Harness, tenant: uuid.UUID
) -> None:
    owner, dashboard, _ = await _pinned_dashboard(harness, tenant)
    created = await _share(harness, owner, dashboard, expires_in_hours=24)
    assert created.status_code == 201, created.text
    body = created.json()
    token = body["token"]
    assert len(token) == 43 and body["url"].endswith(token) and body["active"] is True
    expires = dt.datetime.fromisoformat(body["expires_at"])
    assert dt.timedelta(hours=23) < expires - dt.datetime.now(dt.UTC) <= dt.timedelta(hours=24)

    guest = await harness.client.get(f"/api/v1/share/{token}")  # no credential at all
    assert guest.status_code == 200, guest.text
    assert guest.headers["cache-control"] == "no-store"
    assert guest.headers["referrer-policy"] == "no-referrer"
    snapshot = guest.json()
    assert set(snapshot) == {"name", "expires_at", "tiles"}
    tile = snapshot["tiles"][0]
    assert set(tile) == {"title", "position", "chart_spec", "overrides", "data", "data_status"}
    assert tile["data_status"] == "ok" and len(tile["data"]["rows"]) == 2
    text = guest.text
    for secret in ("SELECT", "sales.orders", "query-results", str(owner.user_id), str(tenant)):
        assert secret not in text, secret  # no SQL, sources, handles, users or tenant

    listed = await harness.client.get(
        f"/api/v1/dashboards/{dashboard}/share-links", headers=owner.headers
    )
    assert [link["id"] for link in listed.json()["items"]] == [body["id"]]
    assert token not in listed.text
    assert [e.event_type for e in harness.audit.events] == ["dashboard.share_link.created"]


async def test_revoked_expired_and_unknown_tokens_are_the_same_404(
    harness: Harness, tenant: uuid.UUID, platform_db: Any
) -> None:
    owner, dashboard, _ = await _pinned_dashboard(harness, tenant)
    first = (await _share(harness, owner, dashboard)).json()
    second = (await _share(harness, owner, dashboard)).json()

    revoked = await harness.client.delete(
        f"/api/v1/dashboards/{dashboard}/share-links/{first['id']}", headers=owner.headers
    )
    assert revoked.status_code == 204
    await platform_db.execute(
        "UPDATE dashboard.share_links SET expires_at = now() - interval '1 second' WHERE id = $1",
        uuid.UUID(second["id"]),
    )
    for token in (first["token"], second["token"], "A" * 43, "short", "x" * 128):
        response = await harness.client.get(f"/api/v1/share/{token}")
        assert response.status_code == 404, token
        assert response.json()["error"]["code"] == "NOT_FOUND"
    assert harness.audit.events[-1].event_type == "dashboard.share_link.revoked"


async def test_only_the_owner_with_a_fresh_step_up_can_share(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    _, dashboard, _ = await _pinned_dashboard(harness, tenant, visibility="tenant")
    colleague = harness.identity.add_user(tenant, {"developer"}, fresh_mfa=True)
    not_owner = await _share(harness, colleague, dashboard)
    assert not_owner.status_code == 403
    assert not_owner.json()["error"]["code"] == "DASHBOARD_NOT_OWNER"
    stale_owner = harness.identity.add_user(tenant, {"developer"})
    stale_dashboard = await harness.dashboard(stale_owner)
    stale = await _share(harness, stale_owner, stale_dashboard)
    assert stale.status_code == 403 and stale.json()["error"]["code"] == "STEP_UP_REQUIRED"
    client_role = harness.identity.add_user(tenant, {"client"}, fresh_mfa=True)
    client_dashboard = await harness.dashboard(client_role)
    assert (await _share(harness, client_role, client_dashboard)).status_code == 403
    foreign = harness.identity.add_user(other_tenant, {"developer"}, fresh_mfa=True)
    assert (await _share(harness, foreign, dashboard)).status_code == 404
    # Tenant policy (identity-service) can give a client `dashboard:share`.
    sharing_client = harness.identity.add_user(
        tenant, {"client"}, fresh_mfa=True, extra_permissions=frozenset({"dashboard:share"})
    )
    shared_by_client = await _share(
        harness, sharing_client, await harness.dashboard(sharing_client)
    )
    assert shared_by_client.status_code == 201


async def test_links_are_time_boxed_and_limited(harness: Harness, tenant: uuid.UUID) -> None:
    owner, dashboard, _ = await _pinned_dashboard(harness, tenant)
    long = await _share(harness, owner, dashboard, expires_in_hours=720)
    lifetime = dt.datetime.fromisoformat(long.json()["expires_at"]) - dt.datetime.now(dt.UTC)
    assert lifetime <= dt.timedelta(hours=168)  # capped at the maximum
    for _ in range(19):
        assert (await _share(harness, owner, dashboard)).status_code == 201
    over = await _share(harness, owner, dashboard)
    assert over.status_code == 409 and over.json()["error"]["code"] == "SHARE_LINK_LIMIT"


async def test_guest_tiles_without_data_when_expired_or_export_sized(
    harness: Harness, tenant: uuid.UUID
) -> None:
    owner, dashboard, artifact = await _pinned_dashboard(harness, tenant)
    second = await harness.store_artifact(tenant)
    await harness.client.post(
        f"/api/v1/dashboards/{dashboard}/tiles", json={"artifact_id": second}, headers=owner.headers
    )
    token = (await _share(harness, owner, dashboard)).json()["token"]
    first_handle = await _handle(harness, tenant, artifact)
    second_handle = await _handle(harness, tenant, second)
    harness.results.expired.add(first_handle)
    harness.results.row_counts[second_handle] = 50_000
    tiles = (await harness.client.get(f"/api/v1/share/{token}")).json()["tiles"]
    assert sorted(t["data_status"] for t in tiles) == ["expired", "too_large"]
    assert all(t["data"] is None for t in tiles)


async def _handle(harness: Harness, tenant: uuid.UUID, artifact_id: str) -> str:
    """The result handle the fake store hands out for an artifact (read it via the data route)."""
    reader = harness.identity.add_user(tenant, {"developer"}, fresh_mfa=True)
    before = len(harness.results.reads)
    await harness.client.get(f"/api/v1/artifacts/{artifact_id}/data", headers=reader.headers)
    return harness.results.reads[before][1]


async def test_reading_export_sized_results_needs_step_up(
    harness: Harness, tenant: uuid.UUID
) -> None:
    """Section 7.3: downloading raw results above the threshold is a step-up operation."""
    artifact = await harness.store_artifact(tenant)
    handle = await _handle(harness, tenant, artifact)
    harness.results.row_counts[handle] = 10_001
    stale = harness.identity.add_user(tenant, {"client"})
    refused = await harness.client.get(f"/api/v1/artifacts/{artifact}/data", headers=stale.headers)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "STEP_UP_REQUIRED"
    fresh = harness.identity.add_user(tenant, {"client"}, fresh_mfa=True)
    allowed = await harness.client.get(f"/api/v1/artifacts/{artifact}/data", headers=fresh.headers)
    assert allowed.status_code == 200
    harness.results.row_counts[handle] = 10_000  # at the threshold: an ordinary read
    assert (
        await harness.client.get(f"/api/v1/artifacts/{artifact}/data", headers=stale.headers)
    ).status_code == 200


async def test_stopping_a_share_never_needs_more_than_starting_it(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    """Regression (A10 review): list/revoke required `dashboard:share`, so a client whose
    sharing policy was later withdrawn could not revoke the links still live."""
    sharer = harness.identity.add_user(
        tenant, {"client"}, fresh_mfa=True, extra_permissions=frozenset({"dashboard:share"})
    )
    dashboard = await harness.dashboard(sharer)
    link = (await _share(harness, sharer, dashboard)).json()
    # The tenant turns client sharing off: same user, no `dashboard:share` any more.
    same_user = harness.identity.principals[sharer.token]
    same_user["permissions"] = [p for p in same_user["permissions"] if p != "dashboard:share"]
    base = f"/api/v1/dashboards/{dashboard}/share-links"
    assert (await harness.client.get(base, headers=sharer.headers)).status_code == 200
    revoked = await harness.client.delete(f"{base}/{link['id']}", headers=sharer.headers)
    assert revoked.status_code == 204
    assert (await harness.client.get(f"/api/v1/share/{link['token']}")).status_code == 404


async def test_an_org_admin_can_revoke_a_leaked_link_on_any_dashboard(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    owner, dashboard, _ = await _pinned_dashboard(harness, tenant)  # private dashboard
    link = (await _share(harness, owner, dashboard)).json()
    base = f"/api/v1/dashboards/{dashboard}/share-links"
    colleague = harness.identity.add_user(tenant, {"developer"})
    assert (await harness.client.get(base, headers=colleague.headers)).status_code == 404
    foreign_admin = harness.identity.add_user(other_tenant, {"org_admin"})
    assert (await harness.client.get(base, headers=foreign_admin.headers)).status_code == 404
    admin = harness.identity.add_user(tenant, {"org_admin"})  # no step-up needed to revoke
    listed = await harness.client.get(base, headers=admin.headers)
    assert [item["id"] for item in listed.json()["items"]] == [link["id"]]
    assert (
        await harness.client.delete(f"{base}/{link['id']}", headers=admin.headers)
    ).status_code == 204
    assert (await harness.client.get(f"/api/v1/share/{link['token']}")).status_code == 404
    # ...but an org_admin still cannot mint links on someone else's dashboard.
    fresh_admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    assert (await _share(harness, fresh_admin, dashboard)).status_code == 404


async def test_deactivating_a_user_revokes_every_link_they_created(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    """Section 6.7: identity-service's deactivation cascade. A share link is bearer access, so it
    must stop working with its creator's account -- other users' links are untouched."""
    owner, dashboard, _ = await _pinned_dashboard(harness, tenant)
    mine = [(await _share(harness, owner, dashboard)).json() for _ in range(2)]
    colleague, their_dashboard, _ = await _pinned_dashboard(harness, tenant)
    theirs = (await _share(harness, colleague, their_dashboard)).json()
    path = f"/internal/v1/users/{owner.user_id}/share-links/revoke"
    lifecycle = harness.service_headers("identity-service", "dashboard-service:user-lifecycle")

    # Another tenant's cascade touches nothing here (RLS-bound to the tenant in the request).
    foreign = await harness.client.post(
        path, params={"tenant_id": str(other_tenant)}, headers=lifecycle
    )
    assert foreign.json() == {"revoked": 0}
    done = await harness.client.post(path, params={"tenant_id": str(tenant)}, headers=lifecycle)
    assert done.status_code == 200 and done.json() == {"revoked": 2}
    again = await harness.client.post(path, params={"tenant_id": str(tenant)}, headers=lifecycle)
    assert again.json() == {"revoked": 0}  # idempotent: identity may retry
    for link in mine:
        assert (await harness.client.get(f"/api/v1/share/{link['token']}")).status_code == 404
    assert (await harness.client.get(f"/api/v1/share/{theirs['token']}")).status_code == 200
    audit = harness.audit.events[-1]
    assert audit.event_type == "dashboard.share_links.revoked_for_deactivated_user"
    assert sorted(audit.after_state["share_link_ids"]) == sorted(link["id"] for link in mine)  # type: ignore[index]

    # Only identity-service, with the lifecycle scope.
    for headers in (
        harness.service_headers("analytics-orchestrator", "dashboard-service:user-lifecycle"),
        harness.service_headers("identity-service", "dashboard-service:artifacts"),
        {},
    ):
        refused = await harness.client.post(
            path, params={"tenant_id": str(tenant)}, headers=headers
        )
        assert refused.status_code in (401, 403), headers
