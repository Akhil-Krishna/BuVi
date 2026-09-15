"""Phase A4 Definition of Done: the scripted end-to-end flow against the real stack.

"the unsafe-SQL corpus is 100% rejected [unit suite]; a valid SELECT against sample-sales-db
executes, returns a capped result, and writes a query_executions audit row; no credential ever
appears in a response, log, or error message."

The data source is created and synced through api-gateway -> metadata-service (Phase A3). The query
is then sent to query-gateway exactly as its callers will: a service token obtained from
identity-service's client-credentials grant, plus the user's session. Run via
`scripts/test-query-gateway.sh`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import pyotp
from test_login import USERS, api, check, failures, login

QUERY_GATEWAY = os.environ.get("QUERY_GATEWAY_URL", "http://localhost:8003")
IDENTITY = os.environ.get("IDENTITY_DIRECT_URL", "http://localhost:8001")
PGCONTAINER = os.environ.get("PGCONTAINER", "buvi-dev-postgres-1")
LOG_DIR = os.environ.get("BUVI_SERVICE_LOGS")
READER_PASSWORD = "dev-reader-password"  # noqa: S105 - compose throwaway
GATEWAY_CLIENT_SECRET = "dev-gateway-secret"  # noqa: S105 - compose throwaway


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    command = ["docker", *args]
    return subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603


def psql(sql: str, **variables: str) -> str:
    """Run `sql` against the platform database. Values go in as psql variables, never into the text."""
    args = ["exec", "-i", PGCONTAINER, "psql", "-U", "postgres", "-d", "agentic_bi", "-tA", "-q"]
    for name, value in variables.items():
        args += ["-v", f"{name}={value}"]
    command = ["docker", *args]
    completed = subprocess.run(  # noqa: S603
        command, input=sql, capture_output=True, text=True, check=False
    )
    return completed.stdout.strip()


def main() -> int:
    seen: list[httpx.Response] = []

    print("setup: admin login, step-up, data source via api-gateway -> metadata-service")
    admin, _ = login(USERS["org_admin"][0])
    enroll = api("POST", "/api/v1/auth/mfa/enroll", admin)
    api(
        "POST",
        "/api/v1/auth/mfa/verify",
        admin,
        json={"code": pyotp.TOTP(enroll.json()["secret"]).now()},
    )
    tenant_id = api("GET", "/api/v1/auth/session", admin).json()["tenant_id"]
    source = api(
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
    ).json()
    source_id = source["id"]
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
    check(
        "data source synced and active",
        sync.get("ok") is True and sync.get("status") == "active",
        str(sync),
    )

    token_response = httpx.post(
        f"{IDENTITY}/internal/v1/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "api-gateway",
            "client_secret": GATEWAY_CLIENT_SECRET,
            "audience": "query-gateway",
            "scope": "query-gateway:execute",
        },
    )
    check(
        "service token issued for query-gateway:execute",
        token_response.status_code == 200,
        token_response.text,
    )
    service = {"X-Service-Authorization": f"Bearer {token_response.json().get('access_token', '')}"}

    def query(sql: str, headers: dict[str, str] | None = None, **extra: object) -> httpx.Response:
        body = {"database_id": source_id, "sql": sql, "purpose": "sql_editor", **extra}
        response = httpx.post(
            f"{QUERY_GATEWAY}/internal/v1/queries",
            json=body,
            timeout=60,
            headers={
                **(service if headers is None else headers),
                "Cookie": f"buvi_session={admin}",
            },
        )
        seen.append(response)
        return response

    print("valid SELECT: executes, capped, audited, result handle stored")
    revenue = query(
        "SELECT r.name AS region, date_trunc('month', o.order_date) AS month, sum(o.amount) AS revenue "
        "FROM sales.orders o JOIN sales.customers c ON c.id = o.customer_id JOIN sales.regions r ON r.id = c.region_id "
        "WHERE o.status = 'completed' GROUP BY 1, 2 ORDER BY 1, 2",
    )
    check("revenue query 200", revenue.status_code == 200, revenue.text[:300])
    capped = query("SELECT id, amount FROM sales.orders ORDER BY id", max_rows=10)
    body = capped.json() if capped.status_code == 200 else {}
    check(
        "result capped at max_rows and flagged",
        body.get("row_count") == 10 and body.get("truncated") is True,
        capped.text[:300],
    )
    query_id = body.get("query_id", "")
    row = (
        psql(
            "SELECT status || '|' || row_count || '|' || purpose "
            "FROM query_gateway.query_executions WHERE id = :'id'::uuid",
            id=str(uuid.UUID(query_id)),
        )
        if query_id
        else ""
    )
    check("query_executions audit row written", row == "succeeded|10|sql_editor", row)
    handle = body.get("result_handle", "")
    check(
        "result handle in object storage",
        handle == f"s3://query-results/tenants/{tenant_id}/queries/{query_id}.json",
        handle,
    )
    docker(
        "exec",
        "buvi-dev-minio-1",
        "mc",
        "alias",
        "set",
        "local",
        "http://127.0.0.1:9000",
        "minioadmin",
        "minioadmin",
    )
    stat = docker(
        "exec",
        "buvi-dev-minio-1",
        "mc",
        "stat",
        f"local/query-results/tenants/{tenant_id}/queries/{query_id}.json",
    )
    check("result object exists in MinIO", stat.returncode == 0, stat.stderr[:200])
    rule = docker("exec", "buvi-dev-minio-1", "mc", "ilm", "rule", "ls", "local/query-results")
    check(
        "bucket lifecycle rule expires results",
        "query-result-ttl" in rule.stdout or "1 day" in rule.stdout.lower(),
        rule.stdout[:300],
    )

    print("unsafe SQL: rejected, audited, database untouched")
    before = docker(
        "exec",
        "buvi-dev-sample-sales-db-1",
        "psql",
        "-U",
        "postgres",
        "-d",
        "sample_sales",
        "-tAc",
        "SELECT count(*) FROM sales.orders",
    ).stdout.strip()
    for sql, reason in (
        ("DELETE FROM sales.orders", "WRITE_OPERATION"),
        ("SELECT 1; DROP TABLE sales.orders", "MULTIPLE_STATEMENTS"),
        ("SELECT pg_read_file('/etc/passwd')", "FUNCTION_NOT_ALLOWED"),
        ("SELECT * FROM pg_catalog.pg_shadow", "TABLE_NOT_ALLOWED"),
    ):
        rejected = query(sql)
        details = rejected.json().get("error", {}).get("details", {}) if rejected.content else {}
        check(
            f"rejected {reason}",
            rejected.status_code == 422 and details.get("reason") == reason,
            rejected.text[:200],
        )
    after = docker(
        "exec",
        "buvi-dev-sample-sales-db-1",
        "psql",
        "-U",
        "postgres",
        "-d",
        "sample_sales",
        "-tAc",
        "SELECT count(*) FROM sales.orders",
    ).stdout.strip()
    check("sales.orders unchanged", before == after and before != "", f"{before} -> {after}")
    rejected_rows = psql(
        "SELECT count(*) FROM query_gateway.query_executions "
        "WHERE data_source_id = :'id'::uuid AND status = 'rejected'",
        id=str(uuid.UUID(source_id)),
    )
    check("rejections audited", rejected_rows == "4", rejected_rows)

    print("boundaries")
    check(
        "no service token -> 401",
        query("SELECT id FROM sales.orders", headers={}).status_code == 401,
    )
    wrong = httpx.post(
        f"{IDENTITY}/internal/v1/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "metadata-service",
            "client_secret": "dev-metadata-secret",
            "audience": "query-gateway",
            "scope": "query-gateway:execute",
        },
    )
    check(
        "metadata-service cannot obtain query-gateway:execute",
        wrong.status_code == 403,
        wrong.text[:200],
    )

    print("secret hygiene")
    check(
        "no response contains the credential",
        not [r for r in seen if READER_PASSWORD in r.text or "buvi_reader" in r.text],
    )
    if LOG_DIR:
        logs = {p.name: p.read_text(errors="replace") for p in Path(LOG_DIR).glob("*.log")}
        check("four service logs captured", len(logs) == 4, str(sorted(logs)))
        check(
            "no service log contains the credential",
            not [n for n, t in logs.items() if READER_PASSWORD in t],
        )
    dump = docker(
        "exec",
        PGCONTAINER,
        "pg_dump",
        "-U",
        "postgres",
        "-d",
        "agentic_bi",
        "--data-only",
        "-n",
        "query_gateway",
        "-n",
        "metadata",
    )
    check(
        "no platform row contains the credential",
        dump.returncode == 0 and READER_PASSWORD not in dump.stdout,
    )

    print(f"\n{'PASSED' if not failures else 'FAILED'}: {len(failures)} failure(s)")
    for name in failures:
        print(f"  - {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
