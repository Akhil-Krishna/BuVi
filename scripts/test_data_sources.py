"""Phase A3 Definition of Done: the scripted end-to-end flow, over HTTP only.

"an `org_admin`/`developer` can add the `sample-sales-db` compose service as a
connection, test it, sync it, and see tables/columns in the catalog API; secret never
appears in any API response or log; authorization-test triplet passes."

Runs against the real stack -- Keycloak login, api-gateway, identity-service,
metadata-service, Vault, Postgres and the sample-sales-db container. The triplet itself
is the metadata-service integration suite; this flow proves the live wiring, including
the boundary checks the suite cannot see (gateway-only access, real Vault, real logs).

Run via `scripts/test-data-sources.sh`, which prepares the stack and starts the services.
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

METADATA_DIRECT = os.environ.get("METADATA_DIRECT_URL", "http://localhost:8002")
VAULT = os.environ.get("VAULT_ADDR", "http://localhost:8200")
VAULT_TOKEN = os.environ.get("VAULT_DEV_TOKEN", "devroot")
PGCONTAINER = os.environ.get("PGCONTAINER", "buvi-dev-postgres-1")
LOG_DIR = os.environ.get("BUVI_SERVICE_LOGS")

READER_USER = "buvi_reader"
READER_PASSWORD = "dev-reader-password"  # noqa: S105 - compose throwaway, see sample-sales-init
WRONG_PASSWORD = "Wrong-A3-Password-5f2c"  # noqa: S105 - deliberately wrong test value
EXPECTED_TABLES = ["customers", "order_items", "orders", "products", "regions"]


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    """`docker <args>` against the local compose stack."""
    command = ["docker", *args]
    return subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603


def main() -> int:
    seen: list[httpx.Response] = []

    def call(method: str, path: str, session: str | None, **kwargs: object) -> httpx.Response:
        response = api(method, path, session, **kwargs)
        seen.append(response)
        return response

    print("org_admin: log in and step up (credential changes need fresh MFA, Section 7.3)")
    admin, _ = login(USERS["org_admin"][0])
    session_info = call("GET", "/api/v1/auth/session", admin).json()
    tenant_id = session_info["tenant_id"]
    enroll = call("POST", "/api/v1/auth/mfa/enroll", admin)
    check("org_admin: MFA enroll 201", enroll.status_code == 201, enroll.text)
    verify = call(
        "POST",
        "/api/v1/auth/mfa/verify",
        admin,
        json={"code": pyotp.TOTP(enroll.json()["secret"]).now()},
    )
    check("org_admin: MFA verify 200", verify.status_code == 200, verify.text)

    print("data source: create -> credentials -> test -> sync -> catalog")
    created = call(
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
    )
    check(
        "create data source 201 pending",
        created.status_code == 201 and created.json().get("status") == "pending",
        created.text,
    )
    source_id = created.json()["id"]
    base = f"/api/v1/data-sources/{source_id}"

    secret_body = {
        "host": "localhost",
        "port": 5433,
        "username": READER_USER,
        "password": READER_PASSWORD,
        "sslmode": "disable",
    }
    secret = call("POST", f"{base}/secret", admin, json=secret_body)
    check("set credentials 200", secret.status_code == 200, secret.text)

    vault = httpx.get(
        f"{VAULT}/v1/secret/data/tenants/{tenant_id}/datasources/{source_id}",
        headers={"X-Vault-Token": VAULT_TOKEN},
    )
    stored = vault.json().get("data", {}).get("data", {}) if vault.status_code == 200 else {}
    check(
        "credentials stored in Vault at the tenant-scoped path",
        stored.get("password") == READER_PASSWORD,
    )
    query = f"SELECT secret_ref FROM metadata.data_sources WHERE id = '{uuid.UUID(source_id)}'"  # noqa: S608 - a parsed UUID
    secret_ref = docker(
        "exec", PGCONTAINER, "psql", "-U", "postgres", "-d", "agentic_bi", "-tAc", query
    ).stdout.strip()
    check(
        "metadata.data_sources.secret_ref is the Vault path, not the secret",
        secret_ref == f"secret/data/tenants/{tenant_id}/datasources/{source_id}",
        secret_ref,
    )

    tested = call("POST", f"{base}/test", admin)
    body = tested.json() if tested.status_code == 200 else {}
    check(
        "connectivity test ok",
        body.get("ok") is True and body.get("status") == "active",
        tested.text,
    )
    check(
        "test returns sanitized diagnostics only",
        body.get("message") == f"Connected. {len(EXPECTED_TABLES)} tables discovered.",
        str(body.get("message")),
    )

    synced = call("POST", f"{base}/sync", admin)
    sync = synced.json() if synced.status_code == 200 else {}
    check(
        "catalog sync ok",
        sync.get("ok") is True and sync.get("tables_synced") == len(EXPECTED_TABLES),
        synced.text,
    )
    check(
        "foreign keys catalogued",
        sync.get("relationships_synced") == 4,
        str(sync.get("relationships_synced")),
    )

    tables = call("GET", f"{base}/tables", admin).json().get("items", [])
    check(
        "catalog lists the sales tables",
        [t["table_name"] for t in tables] == EXPECTED_TABLES,
        str([t["table_name"] for t in tables]),
    )
    orders = next((t for t in tables if t["table_name"] == "orders"), None)
    if orders:
        detail = call("GET", f"{base}/tables/{orders['id']}", admin).json()
        columns = {c["column_name"]: c for c in detail.get("columns", [])}
        check(
            "orders columns catalogued",
            set(columns) == {"id", "customer_id", "order_date", "status", "amount"},
            str(sorted(columns)),
        )
        check(
            "column type and comment",
            columns.get("amount", {}).get("data_type") == "numeric(12,2)"
            and "USD" in (columns.get("amount", {}).get("description") or ""),
        )
        targets = {
            r["to_column"]["table_name"]
            for r in detail.get("relationships", [])
            if r["from_column"]["table_name"] == "orders"
        }
        check("orders -> customers relationship", targets == {"customers"}, str(targets))

    print("failure path: wrong password is sanitized, then healed")
    call("POST", f"{base}/secret", admin, json={**secret_body, "password": WRONG_PASSWORD})
    wrong = call("POST", f"{base}/test", admin).json()
    check(
        "wrong password -> AUTHENTICATION_FAILED, status error",
        wrong.get("code") == "AUTHENTICATION_FAILED" and wrong.get("status") == "error",
        str(wrong),
    )
    call("POST", f"{base}/secret", admin, json=secret_body)
    healed = call("POST", f"{base}/test", admin).json()
    check(
        "restored credentials -> active",
        healed.get("ok") is True and healed.get("status") == "active",
    )

    print("boundary checks")
    check(
        "unauthenticated GET /data-sources 401",
        call("GET", "/api/v1/data-sources", None).status_code == 401,
    )
    direct = httpx.get(
        f"{METADATA_DIRECT}/api/v1/data-sources", headers={"Cookie": f"buvi_session={admin}"}
    )
    check(
        "metadata-service refuses a call that bypasses the gateway (401)", direct.status_code == 401
    )
    literal = call("POST", f"{base}/secret", admin, json={**secret_body, "host": "169.254.169.254"})
    check(
        "metadata endpoint IP refused (DESTINATION_NOT_ALLOWED)",
        literal.status_code == 422 and literal.json()["error"]["code"] == "DESTINATION_NOT_ALLOWED",
    )
    audit = call("GET", "/api/v1/admin/audit", admin, params={"limit": 200}).json().get("items", [])
    types = {item["event_type"] for item in audit if item.get("resource_id") == source_id}
    check("audit: connection.created recorded", "connection.created" in types, str(types))
    check(
        "audit: connection.secret_rotated recorded",
        "connection.secret_rotated" in types,
        str(types),
    )

    print("secret hygiene: responses, logs, database, audit")
    sensitive = [READER_PASSWORD, WRONG_PASSWORD]
    leaked = [r.request.url.path for r in seen if any(s in r.text for s in sensitive)]
    check("no API response contains a credential", not leaked, str(leaked))
    if LOG_DIR:
        logs = {p.name: p.read_text(errors="replace") for p in Path(LOG_DIR).glob("*.log")}
        check("service logs were captured", len(logs) == 3, str(sorted(logs)))
        dirty = [name for name, text in logs.items() if any(s in text for s in sensitive)]
        check("no service log (DEBUG level) contains a credential", not dirty, str(dirty))
    else:
        print("  [SKIP] service logs not captured (run via scripts/test-data-sources.sh)")
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
        "metadata",
        "-n",
        "identity",
    )
    check("database dump taken", dump.returncode == 0, dump.stderr[:200])
    check(
        "no platform database row contains a credential",
        not any(s in dump.stdout for s in sensitive),
    )

    print(f"\n{'PASSED' if not failures else 'FAILED'}: {len(failures)} failure(s)")
    for name in failures:
        print(f"  - {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
