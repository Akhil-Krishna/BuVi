"""Phase A7 Definition of Done: the scripted end-to-end flow against the real stack.

"a defined 'Revenue' metric is used by the chat flow instead of the agent guessing an aggregation
expression; groundedness eval shows metric usage tracked per run; GET/POST /semantic/metrics is
fully testable over HTTP."

The metric here is "Average order value" = AVG(amount). Before it is approved the agent
guesses SUM(amount); once approved, the run must compute AVG(amount) -- checked on the SQL
query-gateway actually executed and against the sample database itself.
"""

from __future__ import annotations

import sys
from decimal import Decimal

from test_analytics_run import SECTION_32, admin_with_sample_sales, post_message, sse_events
from test_dashboards import client_session
from test_login import USERS, api, check, failures, login
from test_query_gateway import docker, psql

MESSAGE = "Show average order value by month for Q2"
METRICS = "/api/v1/semantic/metrics"


def executed_sql(run_id: str) -> str:
    return psql(
        "SELECT sql_text FROM query_gateway.query_executions "
        "WHERE run_id = :'id'::uuid AND status = 'succeeded'",
        id=run_id,
    )


def grounding(run_id: str) -> str:
    return psql(
        "SELECT flow_state->'grounding' FROM analytics.runs WHERE id = :'id'::uuid", id=run_id
    )


def run_chat(client: str) -> tuple[str, list[str], str | None]:
    conversation = api("POST", "/api/v1/conversations", client, json={"title": "AOV"}).json()["id"]
    run_id = post_message(client, conversation, MESSAGE).json()["run_id"]
    events = list(sse_events(client, run_id))
    artifact = next(
        (e.get("artifactId") for e in events if e["_event"] == "artifact.completed"), None
    )
    return run_id, [f"{e['stage']}.{e['status']}" for e in events], artifact


def main() -> int:
    admin, source_id = admin_with_sample_sales()
    developer, _ = login(USERS["developer"][0])
    client = client_session(admin)

    print("GET/POST /semantic/metrics over HTTP")
    tables = api(
        "GET", f"/api/v1/data-sources/{source_id}/tables", developer, params={"limit": 200}
    )
    orders = next(t for t in tables.json()["items"] if t["table_name"] == "orders")
    stale = api("GET", METRICS, developer, params={"limit": 200}).json()["items"]
    for metric in stale:
        if metric["name"] == "Average order value" and metric["status"] == "approved":
            api("POST", f"{METRICS}/{metric['id']}/deprecate", developer)
    name = f"Average order value {source_id[:8]}"
    bad = api(
        "POST",
        METRICS,
        developer,
        json={"name": name, "expression": "AVG(amount) + 1", "base_table_id": orders["id"]},
    )
    check("free SQL in an expression is refused (422)", bad.status_code == 422, bad.text)
    created = api(
        "POST",
        METRICS,
        developer,
        json={
            "name": name,
            "expression": "avg(orders.amount)",
            "base_table_id": orders["id"],
            "synonyms": ["average order value", "aov"],
            "default_grain": "month",
        },
    )
    check(
        "metric created as draft, expression normalized",
        created.status_code == 201
        and created.json()["status"] == "draft"
        and created.json()["expression"] == "AVG(amount)",
        created.text,
    )
    metric_id = created.json()["id"]
    listed = api("GET", METRICS, developer, params={"status": "draft", "limit": 200}).json()[
        "items"
    ]
    check("draft listed", metric_id in {m["id"] for m in listed})
    check("client cannot manage metrics (403)", api("GET", METRICS, client).status_code == 403)

    print("before approval: drafts are never used; the agent guesses")
    run_id, names, _ = run_chat(client)
    check("run completed with the Section 32 sequence", names == SECTION_32, str(names))
    sql = executed_sql(run_id).lower()
    check("guessed aggregation (SUM) executed", "sum(" in sql and "avg(" not in sql, sql)
    check(
        "no metric recorded for the run", '"metric_ids": []' in grounding(run_id), grounding(run_id)
    )

    print("approve -> the chat flow uses the definition")
    approved = api("POST", f"{METRICS}/{metric_id}/approve", admin)
    check(
        "approved by org_admin",
        approved.status_code == 200 and approved.json()["status"] == "approved",
        approved.text,
    )
    run_id, names, artifact_id = run_chat(client)
    check("run completed with the Section 32 sequence", names == SECTION_32, str(names))
    sql = executed_sql(run_id).lower()
    check(
        "executed SQL computes AVG(amount), not a guess", "avg(" in sql and "sum(" not in sql, sql
    )
    record = grounding(run_id)
    check(
        "groundedness tracked per run: metric used",
        metric_id in record and '"measures_from_metrics": 1' in record,
        record,
    )
    check("insight grounded in the result statistics", '"insight_grounded": true' in record, record)
    artifact = api("GET", f"/api/v1/artifacts/{artifact_id}", client).json()
    data = api("GET", f"/api/v1/artifacts/{artifact_id}/data", client).json()
    truth = docker(
        "exec",
        "buvi-dev-sample-sales-db-1",
        "psql",
        "-U",
        "postgres",
        "-d",
        "sample_sales",
        "-tA",
        "-c",
        "SELECT round(avg(amount), 2) FROM sales.orders WHERE order_date >= DATE '2026-04-01' "
        "AND order_date < DATE '2026-07-01' GROUP BY date_trunc('month', order_date) ORDER BY 1",
    ).stdout.split()
    got = sorted(str(round(Decimal(str(row[1])), 2)) for row in data.get("rows", []))
    check(
        "artifact data equals AVG(amount) computed in the source database",
        got == sorted(truth),
        f"{got} vs {truth}",
    )
    headline = psql(
        "SELECT flow_state->'insight'->>'headline' FROM analytics.runs WHERE id = :'id'::uuid",
        id=run_id,
    )
    check(
        "artifact summary is the run's grounded insight",
        bool(headline) and artifact.get("summary") == headline,
        f"{artifact.get('summary')!r} vs {headline!r}",
    )

    print("deprecate -> no longer used; governance audited")
    api("POST", f"{METRICS}/{metric_id}/deprecate", developer)
    run_id, _, _ = run_chat(client)
    check("deprecated metric not used", "sum(" in executed_sql(run_id).lower())
    audited = psql(
        "SELECT string_agg(event_type, ',' ORDER BY created_at) FROM identity.audit_events "
        "WHERE resource_id = :'id'",
        id=metric_id,
    )
    check(
        "metric lifecycle audited",
        audited == "semantic.metric.created,semantic.metric.approved,semantic.metric.deprecated",
        audited,
    )

    if failures:
        print(f"\nFAILED ({len(failures)}): " + "; ".join(failures))
        return 1
    print("\nPhase A7 DoD flow: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
