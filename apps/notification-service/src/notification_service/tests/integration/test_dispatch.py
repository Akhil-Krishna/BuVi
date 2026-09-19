"""Phase A11 DoD, event side: each consumed topic produces the right in-app/email notifications,
exactly once per event, for active recipients only."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from notification_service.application.services.dispatcher import Outcome
from notification_service.tests.conftest import (
    FailingEmail,
    Harness,
    PostgresInfo,
    running,
)
from platform_auth import ServiceTokenIssuer
from platform_contracts import (
    DashboardTilePinned,
    IdentityRoleChanged,
    McpInvocationDenied,
    MetadataSyncCompleted,
)

pytestmark = pytest.mark.integration


async def _rows(db: Any, tenant: uuid.UUID) -> list[tuple[str, str, str, str]]:
    rows = await db.fetch(
        "SELECT user_id::text, channel, template_key, status FROM notification.notifications "
        "WHERE tenant_id = $1 ORDER BY created_at, channel",
        tenant,
    )
    return [(r["user_id"], r["channel"], r["template_key"], r["status"]) for r in rows]


async def test_a_dashboard_pin_notifies_the_pinner_in_app_once(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    pinner = harness.identity.add_user(tenant, {"developer"})
    event = DashboardTilePinned(
        tenant_id=tenant,
        dashboard_id=uuid.uuid4(),
        tile_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        user_id=pinner.user_id,
    )
    first = await harness.dispatch("dashboard.tile.pinned", event, key="DASHBOARD:7")
    assert (first.outcome, first.notifications) == (Outcome.DONE, 1)
    # JetStream redelivers the same message (same stream sequence): nothing new.
    await harness.dispatch("dashboard.tile.pinned", event, key="DASHBOARD:7")
    assert await _rows(platform_db, tenant) == [
        (str(pinner.user_id), "in_app", "dashboard.tile_pinned", "sent")
    ]
    assert harness.email.sent == []

    inbox = (await harness.client.get("/api/v1/me/notifications", headers=pinner.headers)).json()
    [item] = inbox["items"]
    assert item["title"] == "Pinned to your dashboard"
    assert str(event.dashboard_id) in item["body"]
    assert item["payload"]["tile_id"] == str(event.tile_id)
    assert "request_id" not in item["payload"] and inbox["unread"] == 1


async def test_a_denied_mcp_call_alerts_every_active_org_admin_in_app_and_by_email(
    harness: Harness, platform_db: Any, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    admin = harness.identity.add_user(tenant, {"org_admin"})
    second = harness.identity.add_user(tenant, {"org_admin", "developer"})
    gone = harness.identity.add_user(tenant, {"org_admin"})
    harness.identity.inactive.add(str(gone.user_id))
    harness.identity.add_user(tenant, {"developer"})
    harness.identity.add_user(other_tenant, {"org_admin"})
    event = McpInvocationDenied(
        tenant_id=tenant,
        tool_id=uuid.uuid4(),
        reason="not_granted",
        invocation_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )
    handled = await harness.dispatch("mcp.invocation.denied", event)
    assert (handled.outcome, handled.notifications) == (Outcome.DONE, 4)
    assert harness.identity.directory_calls[-1] == {"tenant_id": str(tenant), "role": "org_admin"}
    rows = await _rows(platform_db, tenant)
    assert sorted((r[0], r[1], r[3]) for r in rows) == sorted(
        (str(u.user_id), channel, "sent")
        for u in (admin, second)
        for channel in ("in_app", "email")
    )
    assert sorted(m.to for m in harness.email.sent) == sorted([admin.email, second.email])
    mail = harness.email.sent[0]
    assert mail.subject == "MCP tool call denied"
    assert "not_granted" in mail.body and str(event.tool_id) in mail.body


async def test_a_sync_completion_notifies_whoever_ran_it(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    runner = harness.identity.add_user(tenant, {"developer"})
    for status, tables, title in (
        ("succeeded", 5, "Schema sync finished"),
        ("failed", 0, "Schema sync failed"),
    ):
        event = MetadataSyncCompleted(
            tenant_id=tenant,
            data_source_id=uuid.uuid4(),
            data_source_name="sample-sales-db",
            status=status,  # type: ignore[arg-type]
            tables_synced=tables,
            user_id=runner.user_id,
        )
        await harness.dispatch("metadata.sync.completed", event)
        assert harness.email.sent[-1].subject == title
        assert harness.email.sent[-1].to == runner.email
    assert "5 tables" in harness.email.sent[0].body
    assert [r[1] for r in await _rows(platform_db, tenant)].count("email") == 2


async def test_a_role_change_notifies_the_affected_user(
    harness: Harness, tenant: uuid.UUID
) -> None:
    user = harness.identity.add_user(tenant, {"developer", "auditor"})
    admin = harness.identity.add_user(tenant, {"org_admin"})
    event = IdentityRoleChanged(
        tenant_id=tenant,
        user_id=user.user_id,
        roles=("auditor", "developer"),
        granted=("auditor",),
        revoked=(),
        changed_by=admin.user_id,
    )
    await harness.dispatch("identity.role.changed", event)
    [mail] = harness.email.sent
    assert (mail.to, mail.subject) == (user.email, "Your roles changed")
    assert "Granted: auditor" in mail.body and "Revoked: none" in mail.body


async def test_malformed_and_future_major_events_are_dropped(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    dispatcher = harness.app.state.dispatcher
    assert (await dispatcher.handle("dashboard.tile.pinned", "K:1", b"{")).outcome is Outcome.DROP
    future = (
        b'{"schema_version": "2.0", "tenant_id": "%s", "dashboard_id": "%s", "tile_id": "%s",'
        b' "artifact_id": "%s", "user_id": "%s"}'
        % tuple(str(x).encode() for x in (tenant, *(uuid.uuid4() for _ in range(4))))
    )
    assert (await dispatcher.handle("dashboard.tile.pinned", "K:2", future)).outcome is Outcome.DROP
    assert (await dispatcher.handle("query.completed", "K:3", b"{}")).outcome is Outcome.DROP
    assert await _rows(platform_db, tenant) == []


async def test_an_unavailable_directory_retries_the_event_and_writes_nothing(
    harness: Harness, platform_db: Any, tenant: uuid.UUID
) -> None:
    user = harness.identity.add_user(tenant, {"developer"})
    harness.identity.directory_down = True
    event = IdentityRoleChanged(
        tenant_id=tenant,
        user_id=user.user_id,
        roles=("developer",),
        granted=(),
        revoked=("auditor",),
        changed_by=uuid.uuid4(),
    )
    assert (await harness.dispatch("identity.role.changed", event, key="IDENTITY:1")).outcome is (
        Outcome.RETRY
    )
    assert await _rows(platform_db, tenant) == []
    harness.identity.directory_down = False
    await harness.dispatch("identity.role.changed", event, key="IDENTITY:1")
    assert len(await _rows(platform_db, tenant)) == 2


async def test_a_failed_email_is_recorded_and_the_event_still_completes(
    postgres: PostgresInfo, issuer: ServiceTokenIssuer, platform_db: Any, tenant: uuid.UUID
) -> None:
    async with running(postgres, issuer, email=FailingEmail()) as h:
        user = h.identity.add_user(tenant, {"developer"})
        event = MetadataSyncCompleted(
            tenant_id=tenant,
            data_source_id=uuid.uuid4(),
            data_source_name="db",
            status="succeeded",
            tables_synced=1,
            user_id=user.user_id,
        )
        assert (await h.dispatch("metadata.sync.completed", event)).outcome is Outcome.DONE
    statuses = {r[1]: r[3] for r in await _rows(platform_db, tenant)}
    assert statuses == {"in_app": "sent", "email": "failed"}
