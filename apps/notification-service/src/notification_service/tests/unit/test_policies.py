"""Pure rules: signing, routing, templates (Phase A11)."""

from __future__ import annotations

import uuid

import pytest

from notification_service.domain.policies.routing import (
    EMAIL,
    IN_APP,
    TOPICS,
    WEBHOOK_EVENT_TYPES,
    notification_payload,
    route_for,
)
from notification_service.domain.policies.signing import new_signing_secret, sign, verify
from notification_service.domain.policies.templates import render
from platform_contracts import McpInvocationDenied

pytestmark = pytest.mark.unit


def test_signature_covers_timestamp_and_exact_body() -> None:
    secret = new_signing_secret()
    header = sign(secret, 1_700_000_000, b'{"a":1}')
    assert header.startswith("t=1700000000,v1=") and len(header.split("v1=")[1]) == 64
    assert verify(secret, header, b'{"a":1}')
    assert not verify(secret, header, b'{"a":2}')
    assert not verify(secret, header.replace("t=1700000000", "t=1700000001"), b'{"a":1}')
    assert not verify(secret, "garbage", b"")
    assert new_signing_secret() != secret


def test_every_consumed_topic_has_a_route_and_webhooks_are_a_subset() -> None:
    assert set(TOPICS) == {
        "dashboard.tile.pinned",
        "mcp.invocation.denied",
        "metadata.sync.completed",
        "identity.role.changed",
    }
    assert set(TOPICS) > WEBHOOK_EVENT_TYPES
    assert "identity.role.changed" not in WEBHOOK_EVENT_TYPES


def test_security_signal_goes_to_org_admins_without_request_ids() -> None:
    event = McpInvocationDenied(
        tenant_id=uuid.uuid4(),
        tool_id=uuid.uuid4(),
        reason="admin_only",
        invocation_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        request_id="req_1",
    )
    route = route_for("mcp.invocation.denied", event)
    assert (route.role, route.channels, route.user_ids) == ("org_admin", (IN_APP, EMAIL), ())
    payload = notification_payload(event)
    assert "request_id" not in payload and "schema_version" not in payload
    assert render(route.template_key, payload).title == "MCP tool call denied"


def test_unknown_template_renders_generically() -> None:
    assert render("nope", {}).title == "Notification"
