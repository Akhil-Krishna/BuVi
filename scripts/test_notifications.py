"""Phase A11 Definition of Done over HTTP and the queue, through api-gateway, every service real.

"a dashboard pin, a failed MCP invocation, and a sync completion each produce the correct
in-app/email notification (MailHog inbox checked by test), provable over HTTP/queue inspection
alone; a subscribed webhook receives a correctly signed payload for an allow-listed event type
and is rejected for a non-allow-listed destination."

Also: a role change notifies the affected user and never leaves by webhook; the inbox is
owner-only; a disabled webhook receives nothing; `/billing/usage` sums the tokens and query time
the chat run just metered (worker-runtime -> analytics-orchestrator) and counts seats.

The webhook receiver is this script, on localhost:8766 (allow-listed for notification-service in
dev only, like mcp-gateway's sample server).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import threading
import time
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pyotp
from test_analytics_run import post_message, sse_events
from test_login import MAILHOG, USERS, api, check, failures, login
from test_mcp import member_session
from test_query_gateway import READER_PASSWORD, psql

RECEIVER_PORT = 8766
HOOK_URL = f"http://localhost:{RECEIVER_PORT}/buvi-hook"
WEBHOOK_TYPES = ["metadata.sync.completed", "dashboard.tile.pinned", "mcp.invocation.denied"]


class Received:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.items: list[tuple[dict[str, str], bytes]] = []

    def of(self, event_type: str) -> list[tuple[dict[str, str], bytes]]:
        with self.lock:
            return [i for i in self.items if i[0].get("x-buvi-event") == event_type]


RECEIVED = Received()


class Hook(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        with RECEIVED.lock:
            RECEIVED.items.append(({k.lower(): v for k, v in self.headers.items()}, body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args: Any) -> None:
        return


def verify(secret: str, header: str, body: bytes) -> bool:
    """What a receiver does: HMAC-SHA256(secret, "<t>." + body), constant-time compare."""
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        mac = hmac.new(secret.encode(), f"{parts['t']}.".encode() + body, hashlib.sha256)
    except (KeyError, ValueError):
        return False
    return hmac.compare_digest(mac.hexdigest(), parts["v1"])


def wait_for(what: str, probe: Callable[[], Any], seconds: float = 30.0) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.5)
    check(what, False, "timed out")
    return None


def notifications(session: str, template: str) -> list[dict[str, Any]]:
    items = api("GET", "/api/v1/me/notifications", session).json().get("items", [])
    return [i for i in items if i["template_key"] == template]


def mail(email: str, subject: str) -> list[dict[str, Any]]:
    found = httpx.get(
        f"{MAILHOG}/api/v2/search", params={"kind": "to", "query": email}, timeout=5
    ).json()
    return [
        item
        for item in found.get("items") or []
        if subject in item["Content"]["Headers"].get("Subject", [""])[0]
    ]


def signed_delivery(event_type: str, secret: str) -> dict[str, Any]:
    [(headers, body)] = RECEIVED.of(event_type)
    check(
        f"webhook {event_type}: signature verifies with the shown-once secret",
        verify(secret, headers.get("x-buvi-signature", ""), body)
        and not verify(secret + "x", headers.get("x-buvi-signature", ""), body),
        headers.get("x-buvi-signature", ""),
    )
    payload: dict[str, Any] = json.loads(body)
    check(
        f"webhook {event_type}: envelope type and delivery id",
        payload["type"] == event_type and payload["id"] == headers.get("x-buvi-delivery"),
        str(payload)[:300],
    )
    return payload


def main() -> int:
    receiver = ThreadingHTTPServer(("127.0.0.1", RECEIVER_PORT), Hook)
    threading.Thread(target=receiver.serve_forever, daemon=True).start()

    print("setup: org_admin with a fresh TOTP step-up, a stale admin session, developer, client")
    admin, _ = login(USERS["org_admin"][0])
    enroll = api("POST", "/api/v1/auth/mfa/enroll", admin)
    api(
        "POST",
        "/api/v1/auth/mfa/verify",
        admin,
        json={"code": pyotp.TOTP(enroll.json()["secret"]).now()},
    )
    stale, _ = login(USERS["org_admin"][0])
    developer = member_session(admin, "developer")
    developer_id = api("GET", "/api/v1/auth/session", developer).json()["user_id"]
    client = member_session(admin, "client")
    admin_email, developer_email = USERS["org_admin"][1], USERS["developer"][1]

    print("webhooks: org_admin + step-up; Section 15 destinations; event-type allow-list")
    body = {"url": HOOK_URL, "event_types": WEBHOOK_TYPES}
    refused = api("POST", "/api/v1/admin/webhooks", stale, json=body)
    check(
        "stale step-up refused (403 STEP_UP_REQUIRED)",
        refused.status_code == 403 and refused.json()["error"]["code"] == "STEP_UP_REQUIRED",
        refused.text,
    )
    check(
        "developer cannot create a webhook",
        api("POST", "/api/v1/admin/webhooks", developer, json=body).status_code == 403,
    )
    for url, reason in (
        (f"http://127.0.0.1:{RECEIVER_PORT}/buvi-hook", "destination_not_allowed"),
        ("https://169.254.169.254/latest/meta-data", "destination_not_allowed"),
        ("https://10.0.0.1/hook", "destination_not_allowed"),
        ("http://hooks.example.com/hook", "https_required"),
    ):
        bad = (
            api("POST", "/api/v1/admin/webhooks", admin, json={**body, "url": url})
            .json()
            .get("error", {})
        )
        check(
            f"non-allow-listed destination refused: {url}",
            bad.get("code") == "WEBHOOK_URL_INVALID"
            and bad.get("details", {}).get("reason") == reason,
            str(bad),
        )
    wrong = api(
        "POST",
        "/api/v1/admin/webhooks",
        admin,
        json={**body, "event_types": ["identity.role.changed"]},
    )
    check(
        "non-allow-listed event type refused",
        wrong.status_code == 422
        and wrong.json()["error"]["code"] == "WEBHOOK_EVENT_TYPE_NOT_ALLOWED",
        wrong.text,
    )
    created = api("POST", "/api/v1/admin/webhooks", admin, json=body)
    check("webhook created (201, no-store)", created.status_code == 201, created.text)
    hook = created.json()
    secret = hook.get("signing_secret", "")
    check(
        "signing secret shown once, never listed",
        secret.startswith("whsec_")
        and created.headers.get("cache-control") == "no-store"
        and secret not in api("GET", "/api/v1/admin/webhooks", admin).text,
    )
    ref = psql(
        "SELECT signing_secret_ref FROM notification.webhook_subscriptions WHERE id = :'id'::uuid",
        id=hook.get("id", str(uuid.uuid4())),
    )
    check("database holds only the Vault reference", ref.endswith(hook.get("id", "?")), ref)

    print("sync completion -> in-app + email to whoever ran it, and a signed webhook")
    source_id = api(
        "POST",
        "/api/v1/data-sources",
        admin,
        json={
            "name": "sample-sales-db",
            "engine": "postgres",
            "host_label": "sample-sales-db (compose)",
            "database_name": "sample_sales",
            "allowed_schemas": ["sales"],
        },
    ).json()["id"]
    api(
        "POST",
        f"/api/v1/data-sources/{source_id}/secret",
        admin,
        json={
            "host": "localhost",
            "port": 5433,
            "username": "buvi_reader",
            "password": READER_PASSWORD,
            "sslmode": "disable",
        },
    )
    sync = api("POST", f"/api/v1/data-sources/{source_id}/sync", admin).json()
    check("data source synced", sync.get("status") == "active", str(sync))
    [item] = wait_for(
        "in-app: sync completion", lambda: notifications(admin, "metadata.sync_completed")
    ) or [{}]
    check(
        "in-app sync notification names the source and table count",
        item.get("title") == "Schema sync finished"
        and "sample-sales-db" in item.get("body", "")
        and "5 tables" in item.get("body", ""),
        str(item),
    )
    wait_for("email: sync completion in MailHog", lambda: mail(admin_email, "Schema sync finished"))
    wait_for("webhook: sync completion", lambda: RECEIVED.of("metadata.sync.completed"))
    delivered = signed_delivery("metadata.sync.completed", secret)
    check(
        "webhook payload is the event (ids and counts only)",
        delivered["data"].get("data_source_id") == source_id
        and delivered["data"].get("tables_synced") == 5
        and "request_id" not in delivered["data"],
        str(delivered),
    )

    print("dashboard pin (Section 32 run as client) -> in-app for the pinner, and a webhook")
    conversation = api("POST", "/api/v1/conversations", client, json={"title": "Q2"}).json()["id"]
    run_id = post_message(client, conversation).json()["run_id"]
    events = list(sse_events(client, run_id))
    artifact_id = next(
        (e.get("artifactId") for e in events if e["_event"] == "artifact.completed"), None
    )
    check("run produced an artifact", bool(artifact_id), str(events[-1:]))
    dashboard_id = api("POST", "/api/v1/dashboards", client, json={"name": "Q2"}).json()["id"]
    tile = api(
        "POST",
        f"/api/v1/dashboards/{dashboard_id}/tiles",
        client,
        json={"artifact_id": artifact_id},
    )
    check("tile pinned", tile.status_code == 201, tile.text)
    [pin] = wait_for(
        "in-app: tile pinned", lambda: notifications(client, "dashboard.tile_pinned")
    ) or [{}]
    check(
        "pin notification names the dashboard",
        pin.get("payload", {}).get("dashboard_id") == dashboard_id
        and pin.get("title") == "Pinned to your dashboard",
        str(pin),
    )
    wait_for("webhook: tile pinned", lambda: RECEIVED.of("dashboard.tile.pinned"))
    signed_delivery("dashboard.tile.pinned", secret)

    print("failed MCP invocation -> in-app + email to every org_admin, and a webhook")
    registered = api(
        "POST",
        "/api/v1/mcp/servers",
        admin,
        json={
            "name": f"Unapproved {uuid.uuid4().hex[:6]}",
            "endpoint_url": "http://localhost:8765/mcp",
            "tools": [{"name": "search_docs", "tool_class": "read_metadata"}],
        },
        headers={"Idempotency-Key": f"a11-{uuid.uuid4()}"},
    ).json()
    denied = api(
        "POST",
        f"/api/v1/mcp/servers/{registered['id']}/tools/search_docs/invoke",
        developer,
        json={"arguments": {"query": "x"}},
    )
    check("invocation denied (403)", denied.status_code == 403, denied.text)
    [alert] = wait_for(
        "in-app: MCP denial for the admin", lambda: notifications(admin, "mcp.invocation_denied")
    ) or [{}]
    check(
        "denial alert carries the reason and tool",
        alert.get("payload", {}).get("reason") == "server_not_approved"
        and "server_not_approved" in alert.get("body", ""),
        str(alert),
    )
    check(
        "a developer gets no security alert",
        notifications(developer, "mcp.invocation_denied") == [],
    )
    wait_for("email: MCP denial in MailHog", lambda: mail(admin_email, "MCP tool call denied"))
    wait_for("webhook: MCP denial", lambda: RECEIVED.of("mcp.invocation.denied"))
    signed_delivery("mcp.invocation.denied", secret)

    print("role change -> in-app + email to the affected user; never a webhook")
    for change in ({"grant": ["auditor"]}, {"revoke": ["auditor"]}):
        changed = api("PATCH", f"/api/v1/admin/users/{developer_id}/roles", admin, json=change)
        check(f"role change {change}", changed.status_code == 200, changed.text)
    wait_for(
        "in-app: two role-change notifications",
        lambda: len(notifications(developer, "identity.role_changed")) == 2,
    )
    wait_for("email: role change in MailHog", lambda: mail(developer_email, "Your roles changed"))
    check("role changes never leave by webhook", RECEIVED.of("identity.role.changed") == [])

    print("inbox: owner-only reads")
    mine = notifications(developer, "identity.role_changed")[0]["id"]
    check(
        "the admin cannot mark the developer's notification read (404)",
        api("POST", f"/api/v1/me/notifications/{mine}/read", admin).status_code == 404,
    )
    check(
        "the owner can (204)",
        api("POST", f"/api/v1/me/notifications/{mine}/read", developer).status_code == 204,
    )
    unread = api(
        "GET", "/api/v1/me/notifications", developer, params={"unread_only": "true"}
    ).json()
    check("unread count dropped", mine not in [i["id"] for i in unread["items"]], str(unread))

    print("disable the webhook (step-up) -> no further deliveries")
    check(
        "stale session cannot disable",
        api("DELETE", f"/api/v1/admin/webhooks/{hook['id']}", stale).status_code == 403,
    )
    check(
        "disabled (204)",
        api("DELETE", f"/api/v1/admin/webhooks/{hook['id']}", admin).status_code == 204,
    )
    api("POST", f"/api/v1/data-sources/{source_id}/sync", admin)
    wait_for(
        "second sync notified in-app",
        lambda: len(notifications(admin, "metadata.sync_completed")) == 2,
    )
    check(
        "a disabled webhook receives nothing",
        len(RECEIVED.of("metadata.sync.completed")) == 1,
        str(len(RECEIVED.of("metadata.sync.completed"))),
    )

    print("billing usage: metered by producers, aggregated by worker-runtime")
    tenant = api("GET", "/api/v1/auth/session", admin).json()["tenant_id"]
    wait_for(
        "query time reached analytics.usage_records",
        lambda: (
            psql(
                "SELECT count(*) FROM analytics.usage_records "
                "WHERE tenant_id = :'t'::uuid AND metric = 'query_execution_ms'",
                t=tenant,
            )
            not in ("", "0")
        ),
    )
    usage = api("GET", "/api/v1/billing/usage", admin)
    body_usage = usage.json()
    check(
        "usage: tokens from the chat run, query time, seats",
        usage.status_code == 200
        and body_usage["llm_tokens"]["total"] > 0
        and body_usage["llm_tokens"]["by_stage"]
        and body_usage["query_minutes"] >= 0
        and body_usage["seats"] >= 3,
        usage.text,
    )
    check(
        "a client cannot read billing",
        api("GET", "/api/v1/billing/usage", client).status_code == 403,
    )

    receiver.shutdown()
    if failures:
        print(f"FAILED ({len(failures)}): {'; '.join(failures)}")
        return 1
    print("Phase A11 DoD flow: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
