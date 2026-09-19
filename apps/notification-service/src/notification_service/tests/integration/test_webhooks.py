"""Phase A11 DoD, webhook side: a subscribed webhook receives a correctly signed payload for an
allow-listed event type, and a non-allow-listed destination is refused -- at creation, and at
delivery when DNS changes after approval (Section 15)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from notification_service.domain.policies.signing import SIGNATURE_HEADER, verify
from notification_service.tests.conftest import HOOK_HOST, Caller, Harness
from platform_contracts import DashboardTilePinned, IdentityRoleChanged, MetadataSyncCompleted

pytestmark = [pytest.mark.integration, pytest.mark.security]

URL = f"https://{HOOK_HOST}/buvi"


async def _create(
    harness: Harness, admin: Caller, url: str = URL, events: list[str] | None = None
) -> Any:
    return await harness.client.post(
        "/api/v1/admin/webhooks",
        json={"url": url, "event_types": events or ["metadata.sync.completed"]},
        headers=admin.headers,
    )


def _sync(tenant: uuid.UUID, user: uuid.UUID) -> MetadataSyncCompleted:
    return MetadataSyncCompleted(
        tenant_id=tenant,
        data_source_id=uuid.uuid4(),
        data_source_name="sample-sales-db",
        status="succeeded",
        tables_synced=5,
        user_id=user,
    )


async def _deliveries(db: Any, tenant: uuid.UUID) -> list[tuple[str, str]]:
    rows = await db.fetch(
        "SELECT status, payload->>'delivery' AS detail FROM notification.notifications "
        "WHERE tenant_id = $1 AND channel = 'webhook' ORDER BY created_at",
        tenant,
    )
    return [(r["status"], r["detail"]) for r in rows]


async def test_subscribed_webhook_receives_a_correctly_signed_payload(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    created = await _create(harness, admin)
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    body = created.json()
    secret = body["signing_secret"]
    assert secret.startswith("whsec_") and body["url"] == URL
    assert body["event_types"] == ["metadata.sync.completed"]
    stored = harness.secrets.refs()
    assert stored == {f"tenants/{tenant}/notification/webhooks/{body['id']}"}
    assert harness.audit.types() == ["webhook.created"]
    listed = (await harness.client.get("/api/v1/admin/webhooks", headers=admin.headers)).json()
    assert [w["id"] for w in listed["items"]] == [body["id"]]
    assert "signing_secret" not in listed["items"][0] and secret not in json.dumps(listed)

    event = _sync(tenant, admin.user_id)
    await harness.dispatch("metadata.sync.completed", event)
    [request] = harness.receivers.requests
    # Pinned: sent to the checked address, the hostname only in Host (and TLS SNI).
    assert request.url.host == "93.184.215.20" and request.headers["host"] == HOOK_HOST
    assert request.extensions["sni_hostname"] == HOOK_HOST
    assert request.headers["x-buvi-event"] == "metadata.sync.completed"
    assert verify(secret, request.headers[SIGNATURE_HEADER], request.content)
    assert not verify("whsec_wrong", request.headers[SIGNATURE_HEADER], request.content)
    assert not verify(secret, request.headers[SIGNATURE_HEADER], request.content + b" ")
    delivered = json.loads(request.content)
    assert delivered["type"] == "metadata.sync.completed"
    assert (
        delivered["tenant_id"] == str(tenant)
        and delivered["id"] == request.headers["x-buvi-delivery"]
    )
    assert delivered["data"]["data_source_id"] == str(event.data_source_id)
    assert await _deliveries(platform_db, tenant) == [("sent", "delivered:1")]

    # An event type the subscription did not ask for is not delivered.
    await harness.dispatch(
        "dashboard.tile.pinned",
        DashboardTilePinned(
            tenant_id=tenant,
            dashboard_id=uuid.uuid4(),
            tile_id=uuid.uuid4(),
            artifact_id=uuid.uuid4(),
            user_id=admin.user_id,
        ),
    )
    assert len(harness.receivers.requests) == 1


async def test_non_allow_listed_destinations_and_event_types_are_refused(
    harness: Harness, tenant: uuid.UUID
) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    for url, reason in (
        ("https://10.0.0.5/hook", "destination_not_allowed"),
        ("https://169.254.169.254/latest", "destination_not_allowed"),
        ("https://127.0.0.1:9000/hook", "destination_not_allowed"),
        (f"http://{HOOK_HOST}/hook", "https_required"),
        (f"https://user:pw@{HOOK_HOST}/hook", "credentials_in_url"),
        ("https://2130706433/hook", "numeric_host"),
    ):
        refused = await _create(harness, admin, url)
        assert refused.status_code == 422, url
        error = refused.json()["error"]
        assert (error["code"], error["details"]["reason"]) == ("WEBHOOK_URL_INVALID", reason), url
    wrong_type = await _create(harness, admin, events=["identity.role.changed"])
    assert wrong_type.status_code == 422
    assert wrong_type.json()["error"]["code"] == "WEBHOOK_EVENT_TYPE_NOT_ALLOWED"
    assert harness.secrets.refs() == set() and harness.audit.types() == []


async def test_a_destination_that_rebinds_to_a_private_address_is_never_contacted(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    assert (await _create(harness, admin)).status_code == 201
    harness.dns.records[HOOK_HOST] = ["10.1.2.3"]  # after approval: DNS rebinding
    await harness.dispatch("metadata.sync.completed", _sync(tenant, admin.user_id))
    assert harness.receivers.requests == []
    assert await _deliveries(platform_db, tenant) == [("failed", "destination_not_allowed:1")]


async def test_a_failing_receiver_gets_bounded_attempts_and_a_recorded_failure(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    assert (await _create(harness, admin)).status_code == 201
    harness.receivers.status = 500
    await harness.dispatch("metadata.sync.completed", _sync(tenant, admin.user_id))
    assert len(harness.receivers.requests) == 3
    harness.receivers.status = 302
    await harness.dispatch("metadata.sync.completed", _sync(tenant, admin.user_id))
    assert len(harness.receivers.requests) == 4  # a redirect is final, and never followed
    assert await _deliveries(platform_db, tenant) == [
        ("failed", "status_500:3"),
        ("failed", "redirect_refused:1"),
    ]


async def test_webhook_management_needs_org_admin_and_step_up(
    harness: Harness, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    developer = harness.identity.add_user(tenant, {"developer"}, fresh_mfa=True)
    assert (await _create(harness, developer)).status_code == 403
    assert (
        await harness.client.get("/api/v1/admin/webhooks", headers=developer.headers)
    ).status_code == 403
    stale = harness.identity.add_user(tenant, {"org_admin"})
    refused = await _create(harness, stale)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "STEP_UP_REQUIRED"

    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    hook = (await _create(harness, admin)).json()
    foreign_admin = harness.identity.add_user(other_tenant, {"org_admin"}, fresh_mfa=True)
    assert (
        await harness.client.delete(
            f"/api/v1/admin/webhooks/{hook['id']}", headers=foreign_admin.headers
        )
    ).status_code == 404
    assert (
        await harness.client.delete(f"/api/v1/admin/webhooks/{hook['id']}", headers=stale.headers)
    ).status_code == 403
    gone = await harness.client.delete(
        f"/api/v1/admin/webhooks/{hook['id']}", headers=admin.headers
    )
    assert gone.status_code == 204
    assert harness.secrets.refs() == set()
    assert harness.audit.types() == ["webhook.created", "webhook.disabled"]
    listed = (await harness.client.get("/api/v1/admin/webhooks", headers=admin.headers)).json()
    assert listed["items"][0]["status"] == "disabled"
    # A disabled subscription receives nothing.
    await harness.dispatch("metadata.sync.completed", _sync(tenant, admin.user_id))
    assert harness.receivers.requests == []


async def test_active_webhooks_are_capped_per_tenant(
    postgres: Any, issuer: Any, tenant: uuid.UUID
) -> None:
    from notification_service.tests.conftest import running

    async with running(postgres, issuer, max_webhooks_per_tenant=1) as h:
        admin = h.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
        assert (await _create(h, admin)).status_code == 201
        over = await _create(h, admin)
        assert over.status_code == 409 and over.json()["error"]["code"] == "WEBHOOK_LIMIT_REACHED"


async def test_role_change_events_never_leave_by_webhook(
    harness: Harness, tenant: uuid.UUID
) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"}, fresh_mfa=True)
    assert (await _create(harness, admin, events=["metadata.sync.completed"])).status_code == 201
    await harness.dispatch(
        "identity.role.changed",
        IdentityRoleChanged(
            tenant_id=tenant,
            user_id=admin.user_id,
            roles=("org_admin",),
            granted=(),
            revoked=("developer",),
            changed_by=admin.user_id,
        ),
    )
    assert harness.receivers.requests == []
