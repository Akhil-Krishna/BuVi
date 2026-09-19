"""Section 24 controls checked against the running stack (Phase A12; `make backend-e2e`)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import httpx
import pytest

pytestmark = [pytest.mark.system, pytest.mark.security]

PLATFORM_DB = os.environ.get("PGCONTAINER", "buvi-dev-postgres-1")
SAMPLE_PG = os.environ.get("SAMPLE_PG_CONTAINER", "buvi-dev-sample-sales-db-1")
SAMPLE_MYSQL = os.environ.get("SAMPLE_MYSQL_CONTAINER", "buvi-dev-sample-sales-mysql-1")
KEYCLOAK = os.environ.get("KEYCLOAK_URL", "http://localhost:8080")
#: Every service schema (Section 8), as the SQL list the checks below filter on.
SERVICE_SCHEMAS = (
    "('identity', 'metadata', 'semantic', 'analytics', 'query_gateway', 'dashboard', 'mcp', "
    "'notification')"
)
DOCKER = shutil.which("docker") or "docker"


def _psql(container: str, database: str, sql: str) -> list[list[str]]:
    result = subprocess.run(  # noqa: S603
        [
            DOCKER,
            "exec",
            "-i",
            container,
            "psql",
            "-U",
            "postgres",
            "-d",
            database,
            "-qAt",
            "-F",
            "\t",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.split("\t") for line in result.stdout.splitlines() if line]


def test_every_service_table_has_forced_rls() -> None:
    """Section 19: every table in a service schema is RLS-enabled *and* forced (so even the
    owning role is filtered), except Alembic's version table."""
    rows = _psql(
        PLATFORM_DB,
        "agentic_bi",
        "SELECT n.nspname || '.' || c.relname, c.relrowsecurity, c.relforcerowsecurity "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind = 'r' AND c.relname <> 'alembic_version' AND n.nspname IN "
        + SERVICE_SCHEMAS,
    )
    assert len(rows) >= 35, rows  # every service's tables were found
    missing = [name for name, enabled, forced in rows if (enabled, forced) != ("t", "t")]
    assert not missing, f"tables without forced RLS: {missing}"


def test_the_request_path_role_cannot_bypass_rls() -> None:
    [[superuser, bypass]] = _psql(
        PLATFORM_DB,
        "agentic_bi",
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'buvi_app'",
    )
    assert (superuser, bypass) == ("f", "f")
    owned = _psql(
        PLATFORM_DB,
        "agentic_bi",
        "SELECT count(*) FROM pg_tables WHERE tableowner = 'buvi_app' AND schemaname IN "  # noqa: S608 - constant
        + SERVICE_SCHEMAS,
    )
    assert owned == [["0"]], "buvi_app owns tables (owners skip non-forced RLS)"


def test_query_principals_are_read_only() -> None:
    """Section 13: customer queries run as a least-privilege, read-only principal."""
    [[superuser, bypass, create_role, create_db]] = _psql(
        SAMPLE_PG,
        "sample_sales",
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles "
        "WHERE rolname = 'buvi_reader'",
    )
    assert (superuser, bypass, create_role, create_db) == ("f", "f", "f", "f")
    privileges = {
        row[0]
        for row in _psql(
            SAMPLE_PG,
            "sample_sales",
            "SELECT DISTINCT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'buvi_reader'",
        )
    }
    assert privileges == {"SELECT"}
    grants = subprocess.run(  # noqa: S603
        [
            DOCKER,
            "exec",
            SAMPLE_MYSQL,
            "sh",
            "-c",
            'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "SHOW GRANTS FOR buvi_reader" 2>/dev/null',
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    statements = [line for line in grants.splitlines() if line.strip()]
    assert statements, "MySQL reader has no grants listed"
    for statement in statements:
        granted = statement.split(" ON ", 1)[0].removeprefix("GRANT ").split(", ")
        assert set(granted) <= {"USAGE", "SELECT"}, statement


def test_the_result_bucket_expires_objects() -> None:
    """Section 8.5: result handles expire; the bucket's lifecycle rule does the expiring."""
    from minio import Minio

    client = Minio(
        os.environ.get("MINIO_ENDPOINT", "localhost:9000"),
        access_key="minioadmin",
        secret_key="minioadmin",
        secure=False,
    )
    bucket = os.environ.get("RESULT_BUCKET", "query-results")
    lifecycle = client.get_bucket_lifecycle(bucket)
    assert lifecycle is not None, f"{bucket} has no lifecycle rule"
    days = [
        rule.expiration.days
        for rule in lifecycle.rules
        if rule.status == "Enabled" and rule.expiration and rule.expiration.days
    ]
    assert days and min(days) <= 7, days


def test_the_idp_locks_an_account_after_repeated_password_failures() -> None:
    """Section 24: per-account limits on login. Passwords are Keycloak's (Section 6.1)."""
    token = httpx.post(
        f"{KEYCLOAK}/realms/master/protocol/openid-connect/token",
        data={
            "client_id": "admin-cli",
            "username": "admin",
            "password": "admin",
            "grant_type": "password",
        },
        timeout=10,
    ).json()["access_token"]
    realm = httpx.get(
        f"{KEYCLOAK}/admin/realms/{os.environ.get('BUVI_REALM', 'buvi')}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    ).json()
    assert realm["bruteForceProtected"] is True
    assert 0 < int(realm["failureFactor"]) <= 5
    assert json.dumps(realm).count("permanentLockout") == 1  # present and explicit
