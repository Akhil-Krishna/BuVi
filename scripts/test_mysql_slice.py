"""Phase A8 Definition of Done: the vertical slice (Section 32) against a second engine, over HTTP.

"the vertical-slice journey succeeds against MySQL, proven over HTTP, including an approved metric
(Phase A7) applied in MySQL SQL."

A tenant with two active data sources (the Postgres sample and its MySQL twin, same rows) picks
the MySQL one per message. The answer is checked against MySQL itself and against the Postgres
twin -- same data, two engines, one answer.
"""

from __future__ import annotations

import sys
import uuid
from decimal import Decimal

from test_analytics_run import SECTION_32, admin_with_sample_sales, sse_events
from test_dashboards import client_session
from test_login import USERS, api, check, failures, login
from test_query_gateway import READER_PASSWORD, docker, psql

MESSAGE = "Create a sales dashboard for Q2 with monthly revenue"


def mysql_source(admin: str) -> str:
    source = api(
        "POST",
        "/api/v1/data-sources",
        admin,
        json={
            "name": "sample-sales-mysql",
            "engine": "mysql",
            "host_label": "sample-sales-mysql (compose)",
            "database_name": "sales",
            "allowed_schemas": ["sales"],
        },
    )
    check("MySQL data source created", source.status_code == 201, source.text)
    source_id = source.json()["id"]
    api(
        "POST",
        f"/api/v1/data-sources/{source_id}/secret",
        admin,
        json={
            "host": "localhost",
            "port": 3307,
            "username": "buvi_reader",
            "password": READER_PASSWORD,
            "sslmode": "disable",
        },
    )
    tested = api("POST", f"/api/v1/data-sources/{source_id}/test", admin).json()
    check(
        "connectivity test: sanitized, 5 tables",
        tested.get("ok") is True and tested.get("message") == "Connected. 5 tables discovered.",
        str(tested),
    )
    synced = api("POST", f"/api/v1/data-sources/{source_id}/sync", admin).json()
    check("MySQL catalog synced", synced.get("status") == "active", str(synced))
    return str(source_id)


def main() -> int:
    admin, _postgres_id = admin_with_sample_sales()  # the second active source
    mysql_id = mysql_source(admin)
    developer, _ = login(USERS["developer"][0])
    client = client_session(admin)

    print("catalog: MySQL tables and column types through the same API")
    tables = api("GET", f"/api/v1/data-sources/{mysql_id}/tables", developer, params={"limit": 200})
    by_name = {t["table_name"]: t for t in tables.json()["items"]}
    check(
        "five sales tables catalogued",
        set(by_name) == {"customers", "order_items", "orders", "products", "regions"},
    )
    orders = api(
        "GET", f"/api/v1/data-sources/{mysql_id}/tables/{by_name['orders']['id']}", developer
    ).json()
    types = {c["column_name"]: c["data_type"] for c in orders.get("columns", [])}
    check(
        "MySQL column types kept",
        types.get("amount") == "decimal(12,2)" and types.get("order_date") == "date",
        str(types),
    )

    print("approved metric on the MySQL table")
    name = f"Revenue {mysql_id[:8]}"
    created = api(
        "POST",
        "/api/v1/semantic/metrics",
        developer,
        json={
            "name": name,
            "expression": "SUM(amount)",
            "base_table_id": by_name["orders"]["id"],
            "synonyms": ["revenue", "sales"],
        },
    )
    check("metric defined", created.status_code == 201, created.text)
    approved = api("POST", f"/api/v1/semantic/metrics/{created.json()['id']}/approve", admin)
    check("metric approved", approved.status_code == 200, approved.text)

    print("Section 32 journey against MySQL, as a client")
    conversation = api("POST", "/api/v1/conversations", client, json={"title": "MySQL Q2"}).json()[
        "id"
    ]
    ambiguous = api(
        "POST",
        f"/api/v1/conversations/{conversation}/messages",
        client,
        json={"content": MESSAGE},
        headers={"Idempotency-Key": f"a8-{uuid.uuid4()}"},
    )
    run_id = ambiguous.json().get("run_id")
    events = list(sse_events(client, run_id)) if run_id else []
    check(
        "two active sources and none chosen: the run asks for a choice",
        [f"{e['stage']}.{e['status']}" for e in events][-1:] == ["run.failed"],
        str(events[-1:]),
    )
    posted = api(
        "POST",
        f"/api/v1/conversations/{conversation}/messages",
        client,
        json={"content": MESSAGE, "data_source_id": mysql_id},
        headers={"Idempotency-Key": f"a8-{uuid.uuid4()}"},
    )
    check("message 202 with run_id", posted.status_code == 202, posted.text)
    run_id = posted.json()["run_id"]
    events = list(sse_events(client, run_id))
    names = [f"{e['stage']}.{e['status']}" for e in events]
    check("SSE sequence is Section 32", names == SECTION_32, str(names))
    artifact_id = next(
        (e.get("artifactId") for e in events if e["_event"] == "artifact.completed"), None
    )

    executed = psql(
        "SELECT sql_text FROM query_gateway.query_executions WHERE run_id = :'id'::uuid AND status = 'succeeded'",
        id=run_id,
    )
    check(
        "MySQL SQL executed (DATE_FORMAT, not date_trunc)",
        "DATE_FORMAT" in executed and "date_trunc" not in executed,
        executed,
    )
    grounding = psql(
        "SELECT flow_state->'grounding'->>'measures_from_metrics' FROM analytics.runs WHERE id = :'id'::uuid",
        id=run_id,
    )
    check("the approved metric grounded the MySQL query", grounding == "1", grounding)

    data = api("GET", f"/api/v1/artifacts/{artifact_id}/data", client).json()
    got = sorted(str(round(Decimal(str(row[1])), 2)) for row in data.get("rows", []))
    in_mysql = docker(
        "exec",
        "buvi-dev-sample-sales-mysql-1",
        "mysql",
        "-uroot",
        "-psales",
        "-N",
        "-B",
        "sales",
        "-e",
        "SELECT ROUND(SUM(amount), 2) FROM orders WHERE order_date >= '2026-04-01' AND order_date < '2026-07-01' "
        "GROUP BY DATE_FORMAT(order_date, '%Y-%m') ORDER BY 1",
    ).stdout.split()
    in_postgres = docker(
        "exec",
        "buvi-dev-sample-sales-db-1",
        "psql",
        "-U",
        "postgres",
        "-d",
        "sample_sales",
        "-tA",
        "-c",
        "SELECT round(sum(amount), 2) FROM sales.orders WHERE order_date >= DATE '2026-04-01' "
        "AND order_date < DATE '2026-07-01' GROUP BY date_trunc('month', order_date) ORDER BY 1",
    ).stdout.split()
    check(
        "artifact data equals MySQL's own answer", got == sorted(in_mysql), f"{got} vs {in_mysql}"
    )
    check(
        "and equals the Postgres twin's answer (same data, two engines)",
        got == sorted(in_postgres),
        f"{got} vs {in_postgres}",
    )

    dashboard = api("POST", "/api/v1/dashboards", client, json={"name": "MySQL Q2"}).json()["id"]
    tile = api(
        "POST", f"/api/v1/dashboards/{dashboard}/tiles", client, json={"artifact_id": artifact_id}
    )
    check("pinned to a dashboard", tile.status_code == 201, tile.text)

    if failures:
        print(f"\nFAILED ({len(failures)}): " + "; ".join(failures))
        return 1
    print("\nPhase A8 DoD flow: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
