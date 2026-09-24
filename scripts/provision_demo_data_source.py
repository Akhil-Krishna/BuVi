"""Idempotently connect `sample-sales-db` for the demo tenant (Section 32).

Phase B2's chat vertical slice needs an active data source to run against, exactly like
`scripts/test_analytics_run.py`'s own `admin_with_sample_sales()` -- but Phase B3 (the
`(developer)/data` UI that would otherwise do this) is not built yet, so `web/next-app`'s
Playwright suite (`e2e/chat-and-dashboards.spec.ts`) shells out to this instead. Safe to run
against a tenant that already has it connected: exits early rather than creating a duplicate.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pyotp

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_login import USERS, api, login  # noqa: E402
from test_query_gateway import READER_PASSWORD  # noqa: E402

NAME = "sample-sales-db"
PG_CONTAINER = "buvi-dev-postgres-1"


def _clear_admin_mfa(email: str) -> None:
    # Other Playwright specs (account.spec.ts, api-keys.spec.ts, ...) also use
    # demo-admin and leave a factor enrolled behind them -- this script must
    # not assume it is starting from a fresh one (`POST /auth/mfa/enroll`
    # refuses a second factor the same way).
    sql = f"""
      DELETE FROM identity.mfa_credentials WHERE user_id IN (
        SELECT id FROM identity.users WHERE email = '{email}'
      );
      UPDATE identity.users SET mfa_enabled = false WHERE email = '{email}';
    """
    subprocess.run(
        ["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", "postgres", "-d", "agentic_bi",
         "-q", "-v", "ON_ERROR_STOP=1"],
        input=sql, text=True, check=True,
    )


def main() -> int:
    admin, _ = login(USERS["org_admin"][0])

    existing = api("GET", "/api/v1/data-sources", admin).json()["items"]
    matching = next((item for item in existing if item["name"] == NAME), None)
    if matching and matching["status"] == "active":
        print(f"{NAME} already connected and active -- nothing to do")
        return 0

    session = admin
    _clear_admin_mfa(USERS["org_admin"][1])
    enroll = api("POST", "/api/v1/auth/mfa/enroll", session)
    if enroll.status_code == 201:
        verify = api(
            "POST",
            "/api/v1/auth/mfa/verify",
            session,
            json={"code": pyotp.TOTP(enroll.json()["secret"]).now()},
        )
        if verify.status_code != 200:
            print(f"error: mfa verify failed: {verify.status_code} {verify.text}", file=sys.stderr)
            return 1

    if matching:
        # A previous run got as far as creating the row (e.g. it failed before
        # `sync`) -- finish it rather than creating a second one by the same name.
        source_id = matching["id"]
    else:
        created = api(
            "POST",
            "/api/v1/data-sources",
            session,
            json={
                "name": NAME,
                "engine": "postgres",
                "host_label": "sample-sales-db (compose)",
                "database_name": "sample_sales",
                "allowed_schemas": ["sales"],
            },
        )
        if created.status_code != 201:
            print(f"error: create failed: {created.status_code} {created.text}", file=sys.stderr)
            return 1
        source_id = created.json()["id"]
    secret = api(
        "POST",
        f"/api/v1/data-sources/{source_id}/secret",
        session,
        json={
            "host": "localhost",
            "port": 5433,
            "username": "buvi_reader",
            "password": READER_PASSWORD,
            "sslmode": "disable",
        },
    )
    if secret.status_code != 200:
        print(f"error: setting secret failed: {secret.status_code} {secret.text}", file=sys.stderr)
        return 1
    sync = api("POST", f"/api/v1/data-sources/{source_id}/sync", session).json()
    if sync.get("status") != "active":
        print(f"error: sync did not become active: {sync}", file=sys.stderr)
        return 1
    print(f"{NAME} connected and synced ({source_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
