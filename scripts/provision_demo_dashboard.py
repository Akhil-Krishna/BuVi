"""Idempotently give the demo tenant one dashboard with a real pinned tile (Section 32).

Phase B8's guest-share proof (`e2e/b8.spec.ts`) needs an existing dashboard to create a share
link against. Creating one from scratch through the browser would just re-prove B2's own
chat-and-pin flow a second time; this instead provisions it once, the same way
`provision_demo_data_source.py` provisions its data source for the same reason. Safe to run
against a tenant that already has it: exits early rather than creating a duplicate.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_analytics_run import post_message, sse_events
from test_login import USERS, api, login

NAME = "B8 Guest Share Demo"
PG_CONTAINER = "buvi-dev-postgres-1"
#: Resolved rather than relied on from PATH, like `tests/system/test_live_controls.py` does.
DOCKER = shutil.which("docker") or "docker"


def _clear_developer_mfa() -> None:
    sql = """
      DELETE FROM identity.mfa_credentials WHERE user_id = (
        SELECT id FROM identity.users WHERE email = 'developer@demo.example.com'
      );
      UPDATE identity.users SET mfa_enabled = false WHERE email = 'developer@demo.example.com';
    """
    subprocess.run(  # noqa: S603 - fixed argv, no shell, container name is a module constant
        [
            DOCKER,
            "exec",
            "-i",
            PG_CONTAINER,
            "psql",
            "-U",
            "postgres",
            "-d",
            "agentic_bi",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=sql,
        text=True,
        check=True,
    )


def main() -> int:
    subprocess.run(  # noqa: S603 - this interpreter, running a script from this same directory
        [sys.executable, str(Path(__file__).with_name("provision_demo_data_source.py"))],
        check=True,
    )
    _clear_developer_mfa()
    developer, _ = login(USERS["developer"][0])

    existing = api("GET", "/api/v1/dashboards", developer).json()["items"]
    matching = next((d for d in existing if d["name"] == NAME), None)
    if matching:
        tiles = api("GET", f"/api/v1/dashboards/{matching['id']}/tiles", developer).json()["items"]
        if tiles:
            print(f"{NAME} already has a pinned tile -- nothing to do")
            return 0
        dashboard_id = matching["id"]
    else:
        dashboard_id = api("POST", "/api/v1/dashboards", developer, json={"name": NAME}).json()[
            "id"
        ]

    conversation = api("POST", "/api/v1/conversations", developer, json={"title": NAME}).json()[
        "id"
    ]
    run_id = post_message(developer, conversation).json()["run_id"]
    events = list(sse_events(developer, run_id))
    artifact_id = next(
        (e.get("artifactId") for e in events if e["_event"] == "artifact.completed"), None
    )
    if not artifact_id:
        print(f"error: run produced no artifact: {events[-1:]}", file=sys.stderr)
        return 1

    tile = api(
        "POST",
        f"/api/v1/dashboards/{dashboard_id}/tiles",
        developer,
        json={"artifact_id": artifact_id},
    )
    if tile.status_code != 201:
        print(f"error: tile pin failed: {tile.status_code} {tile.text}", file=sys.stderr)
        return 1
    print(f"provisioned dashboard {dashboard_id} with tile from artifact {artifact_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
