"""Phase A6 Definition of Done: Section 32 Steps A-D entirely over HTTP, as a `client`-role user.

"message -> SSE events -> GET /artifacts/{id} -> POST /dashboards/{id}/tiles -- and returns the
correct payloads at every step, for a client-role demo token, with zero browser involved ...
Reject an invalid ChartSpec (extra/unknown field) with a test."

Every service runs for real (`scripts/test-dashboards.sh`); the model is the offline scripted
provider. The strict-schema rejection is exercised end to end: a tile override with an unknown key
goes api-gateway -> dashboard-service -> visualization-service and must come back 422.
"""

from __future__ import annotations

import os
import sys

import httpx
from test_analytics_run import SECTION_32, admin_with_sample_sales, post_message, sse_events
from test_login import USERS, api, check, failures, login, mailed_token
from test_query_gateway import READER_PASSWORD, psql

NATS_MONITOR = os.environ.get("NATS_MONITOR_URL", "http://localhost:8222")
LOG_DIR = os.environ.get("BUVI_SERVICE_LOGS")
ARTIFACT_KEYS = {
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


def client_session(admin: str) -> str:
    """Log in the demo `client` user, inviting them first on a fresh stack."""
    username, email = USERS["client"]
    try:
        session, _ = login(username)
        return session
    except RuntimeError:
        pass
    invite = api(
        "POST", "/api/v1/admin/invitations", admin, json={"email": email, "role_key": "client"}
    )
    check("client invited", invite.status_code == 201, invite.text)
    token = mailed_token(email)
    session, _ = login(username, "POST", f"/api/v1/invitations/{token}/accept")
    return session


def stream_messages(stream: str) -> int:
    details = httpx.get(f"{NATS_MONITOR}/jsz", params={"streams": "true"}, timeout=5).json()
    for account in details.get("account_details", []):
        for detail in account.get("stream_detail", []):
            if detail["name"] == stream:
                return int(detail["state"]["messages"])
    return 0


def main() -> int:
    admin, _source_id = admin_with_sample_sales()
    client = client_session(admin)
    me = api("GET", "/api/v1/auth/session", client).json()
    permissions = set(me.get("permissions", []))
    check(
        "client role: chat, artifact read and pin, no SQL",
        {"chat:use", "artifact:read", "dashboard:pin"} <= permissions
        and "sql:execute" not in permissions,
        str(sorted(permissions)),
    )

    print("Step A: POST /conversations/{id}/messages -> run_id")
    conversation = api("POST", "/api/v1/conversations", client, json={"title": "Q2 sales"})
    check("conversation 201", conversation.status_code == 201, conversation.text)
    posted = post_message(client, conversation.json()["id"])
    check("message 202 with run_id", posted.status_code == 202 and "run_id" in posted.json())
    run_id = posted.json()["run_id"]

    print("Step B: GET /runs/{id}/events (SSE)")
    events = list(sse_events(client, run_id))
    names = [f"{e['stage']}.{e['status']}" for e in events]
    check("SSE sequence is Section 32", names == SECTION_32, str(names))
    artifact_id = next(
        (e.get("artifactId") for e in events if e["_event"] == "artifact.completed"), None
    )
    check("artifact.completed carries artifactId", bool(artifact_id))

    print("Step C: GET /artifacts/{id} and its data")
    artifact = api("GET", f"/api/v1/artifacts/{artifact_id}", client)
    check("artifact 200", artifact.status_code == 200, artifact.text)
    body = artifact.json() if artifact.status_code == 200 else {}
    check(
        "artifact has exactly the Section 9.1 fields", set(body) == ARTIFACT_KEYS, str(sorted(body))
    )
    spec = body.get("chart_spec", {})
    check(
        "chart_spec is a line chart of month x revenue",
        spec.get("type") == "line"
        and spec.get("encoding", {}).get("x", {}).get("field") == "month"
        and spec.get("encoding", {}).get("y", {}).get("field") == "revenue",
        str(spec),
    )
    check(
        "result schema, source refs, refresh policy, pin capability",
        [f.get("field") for f in body.get("result_schema", [])] == ["month", "revenue"]
        and body.get("source_refs", [{}])[0].get("tables") == ["sales.orders"]
        and body.get("refresh_policy") == {"mode": "manual"}
        and body.get("can_pin") is True
        and body.get("run_id") == run_id,
        str(body),
    )
    check("artifact never exposes SQL or the result handle", "sql" not in artifact.text.lower())
    data = api("GET", f"/api/v1/artifacts/{artifact_id}/data", client)
    rows = data.json().get("rows", []) if data.status_code == 200 else []
    check(
        "artifact data: Q2 has three monthly rows",
        data.status_code == 200 and len(rows) == 3,
        data.text[:300],
    )
    stored = psql(
        "SELECT count(*) FROM dashboard.artifacts WHERE id = :'id'::uuid AND run_id = :'run'::uuid",
        id=str(artifact_id),
        run=run_id,
    )
    check("artifact stored once in dashboard-service", stored == "1", stored)
    flow_state = psql(
        "SELECT flow_state ? 'artifact' FROM analytics.runs WHERE id = :'id'::uuid", id=run_id
    )
    check("no artifact copy left in the run's flow_state", flow_state == "f", flow_state)

    print("Step D: POST /dashboards, POST /dashboards/{id}/tiles")
    pinned_before = stream_messages("DASHBOARD")
    dashboard = api("POST", "/api/v1/dashboards", client, json={"name": "Q2 sales"})
    check("dashboard 201", dashboard.status_code == 201, dashboard.text)
    dashboard_id = dashboard.json()["id"]
    tile = api(
        "POST",
        f"/api/v1/dashboards/{dashboard_id}/tiles",
        client,
        json={"artifact_id": artifact_id},
    )
    check("tile 201", tile.status_code == 201, tile.text)
    created = tile.json() if tile.status_code == 201 else {}
    check(
        "tile is the Section 16 DashboardTile",
        created.get("dashboard_id") == dashboard_id
        and created.get("artifact_id") == artifact_id
        and created.get("chart_spec_version") == 1
        and created.get("position") == {"x": 0, "y": 0, "w": 6, "h": 4}
        and created.get("overrides") == {},
        str(created),
    )
    detail = api("GET", f"/api/v1/dashboards/{dashboard_id}", client).json()
    check(
        "dashboard lists the tile",
        [t["id"] for t in detail.get("tiles", [])] == [created.get("id")],
    )
    check("dashboard.tile.pinned published", stream_messages("DASHBOARD") == pinned_before + 1)

    print("strict ChartSpec and authorization, end to end")
    rejected = api(
        "PATCH",
        f"/api/v1/tiles/{created.get('id')}",
        client,
        json={"overrides": {"title": "Q2", "onClick": "fetch('//evil')"}},
    )
    check(
        "unknown override key rejected by visualization-service (422 CHART_SPEC_INVALID)",
        rejected.status_code == 422 and rejected.json()["error"]["code"] == "CHART_SPEC_INVALID",
        rejected.text,
    )
    retitled = api(
        "PATCH",
        f"/api/v1/tiles/{created.get('id')}",
        client,
        json={"overrides": {"title": "Q2 revenue"}},
    )
    check("allowed override accepted", retitled.status_code == 200, retitled.text)
    developer, _ = login(USERS["developer"][0])
    hidden = api("GET", f"/api/v1/dashboards/{dashboard_id}", developer)
    check("another user's private dashboard is 404", hidden.status_code == 404, hidden.text)
    foreign = api(
        "POST",
        f"/api/v1/dashboards/{dashboard_id}/tiles",
        developer,
        json={"artifact_id": artifact_id},
    )
    check("another user cannot pin to it (404)", foreign.status_code == 404, foreign.text)

    if LOG_DIR:
        from pathlib import Path

        logs = {p.name: p.read_text(errors="replace") for p in Path(LOG_DIR).glob("*.log")}
        check(
            "no service log contains the data source credential",
            not [n for n, t in logs.items() if READER_PASSWORD in t],
        )

    if failures:
        print(f"\nFAILED ({len(failures)}): " + "; ".join(failures))
        return 1
    print("\nPhase A6 DoD flow: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
