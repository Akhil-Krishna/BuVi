"""Phase A5 Definition of Done: the scripted end-to-end flow against the real stack.

"POST /conversations/{id}/messages with "Create a sales dashboard for Q2 with monthly revenue"
produces a run_id; the SSE stream shows the full user-safe stage sequence (Section 32); a worker or
executor restart mid-run resumes from the last persisted stage instead of restarting; token budget
enforcement test proves a run is failed with RUN_BUDGET_EXCEEDED when the cap is exceeded."

identity, metadata, query-gateway and api-gateway are started by `scripts/test-analytics-run.sh`.
This script owns analytics-orchestrator and worker-runtime so it can kill them mid-run. The model
provider is the scripted development provider (no API key in CI or dev), with simulated latency.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pyotp
from test_login import SERVICE, USERS, api, check, failures, login
from test_query_gateway import READER_PASSWORD, psql

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = Path(os.environ.get("BUVI_SERVICE_LOGS") or tempfile.mkdtemp(prefix="buvi-a5-"))
MESSAGE = "Create a sales dashboard for Q2 with monthly revenue"
SECTION_32 = [
    "intent.started",
    "intent.completed",
    "schema.started",
    "schema.completed",
    "semantic.started",
    "semantic.completed",
    "sql.started",
    "sql.completed",
    "validation.started",
    "validation.completed",
    "execution.started",
    "execution.completed",
    "visualization.started",
    "visualization.completed",
    "artifact.started",
    "artifact.completed",
    "run.completed",
]
LATENCY = "2"


class Service:
    """A service process in its own process group, so SIGKILL takes `uv` and uvicorn together."""

    def __init__(self, name: str, module: str, port: int, env: dict[str, str]) -> None:
        self.name, self.module, self.port, self.env = name, module, port, env
        self.process: subprocess.Popen[bytes] | None = None
        self.starts = 0

    def start(self, **extra_env: str) -> None:
        self.starts += 1
        log = (LOG_DIR / f"{self.name}.{self.starts}.log").open("wb")
        command = [
            "uv",
            "run",
            "--package",
            self.name,
            "uvicorn",
            f"{self.module}.main:create_app",
            "--factory",
            "--port",
            str(self.port),
        ]
        self.process = subprocess.Popen(  # noqa: S603
            command,
            cwd=ROOT / "apps" / self.name,
            env={**os.environ, **self.env, **extra_env},
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        for _ in range(90):
            try:
                if httpx.get(f"http://localhost:{self.port}/health/live", timeout=1).is_success:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        raise RuntimeError(f"{self.name} did not start; see {log.name}")

    def kill(self) -> None:
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()

    def restart(self, **extra_env: str) -> None:
        self.kill()
        self.start(**extra_env)


def sse_events(session: str, run_id: str, last_event_id: str | None = None) -> Iterator[dict]:
    headers = {"Cookie": f"buvi_session={session}", "Accept": "text/event-stream"}
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
    with httpx.stream(
        "GET", f"{SERVICE}/api/v1/runs/{run_id}/events", headers=headers, timeout=120
    ) as response:
        if response.status_code != 200:
            raise RuntimeError(f"SSE {response.status_code}: {response.read()[:300]!r}")
        frame: dict[str, str] = {}
        for line in response.iter_lines():
            if line.startswith(":"):
                continue
            if not line:
                if "data" in frame:
                    event = json.loads(frame["data"])
                    event["_id"], event["_event"] = frame.get("id"), frame.get("event")
                    yield event
                    if event["stage"] == "run":
                        return
                frame = {}
                continue
            key, _, value = line.partition(":")
            frame[key] = value.removeprefix(" ")


def post_message(session: str, conversation_id: str, content: str = MESSAGE) -> httpx.Response:
    return api(
        "POST",
        f"/api/v1/conversations/{conversation_id}/messages",
        session,
        json={"content": content},
        headers={"Idempotency-Key": f"a5-{uuid.uuid4()}"},
    )


def db_events(run_id: str) -> list[str]:
    rows = psql(
        "SELECT stage || '.' || status FROM analytics.run_events WHERE run_id = :'id'::uuid ORDER BY seq",
        id=run_id,
    )
    return rows.splitlines() if rows else []


def run_row(run_id: str) -> dict:
    raw = psql(
        "SELECT json_build_object('status', status, 'error_code', error_code, 'calls', "
        "flow_state->'usage'->>'calls', 'steps', flow_state->'completed_steps') "
        "FROM analytics.runs WHERE id = :'id'::uuid",
        id=run_id,
    )
    return json.loads(raw) if raw else {}


def wait_for(predicate, seconds: float = 120) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.25)
    return False


def admin_with_sample_sales() -> tuple[str, str]:
    """Admin session with step-up, and sample-sales-db created, credentialed and synced."""
    print("setup: admin login, data source sample-sales-db via api-gateway -> metadata-service")
    admin, _ = login(USERS["org_admin"][0])
    enroll = api("POST", "/api/v1/auth/mfa/enroll", admin)
    api(
        "POST",
        "/api/v1/auth/mfa/verify",
        admin,
        json={"code": pyotp.TOTP(enroll.json()["secret"]).now()},
    )
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
    check("data source synced and active", sync.get("status") == "active", str(sync))
    return admin, source_id


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    orchestrator = Service(
        "analytics-orchestrator",
        "analytics_orchestrator",
        8004,
        {"ANALYTICS_LOG_LEVEL": "DEBUG", "ANALYTICS_SCRIPTED_LATENCY_SECONDS": LATENCY},
    )
    worker = Service(
        "worker-runtime",
        "worker_runtime",
        8005,
        {
            "WORKER_LOG_LEVEL": "DEBUG",
            "WORKER_ACK_WAIT_SECONDS": "20",
            "WORKER_RETRY_BASE_SECONDS": "2",
        },
    )
    try:
        return _flow(orchestrator, worker)
    finally:
        worker.kill()
        orchestrator.kill()


def _flow(orchestrator: Service, worker: Service) -> int:
    admin, _source_id = admin_with_sample_sales()

    orchestrator.start()
    worker.start()

    print("1. message -> run_id -> SSE shows the Section 32 sequence")
    conversation = api("POST", "/api/v1/conversations", admin, json={"title": "Q2 sales"})
    check("conversation created 201", conversation.status_code == 201, conversation.text)
    conversation_id = conversation.json()["id"]
    posted = post_message(admin, conversation_id)
    check("message accepted 202 with run_id", posted.status_code == 202, posted.text)
    run_id = posted.json()["run_id"]
    events = list(sse_events(admin, run_id))
    names = [f"{e['stage']}.{e['status']}" for e in events]
    check("SSE sequence is exactly Section 32", names == SECTION_32, str(names))
    check(
        "SSE id is seq and event is <stage>.<status>",
        all(
            e["_id"] == str(e["seq"]) and e["_event"] == f"{e['stage']}.{e['status']}"
            for e in events
        ),
    )
    artifact = next((e for e in events if e["_event"] == "artifact.completed"), {})
    check(
        "artifact.completed carries an artifactId", bool(artifact.get("artifactId")), str(artifact)
    )
    exposed = {k for e in events for k in e} - {"_id", "_event"}
    check(
        "events expose only user-safe fields",
        exposed <= {"runId", "seq", "stage", "status", "message", "artifactId", "createdAt"},
        str(sorted(exposed)),
    )
    check(
        "run row completed with 5 model calls",
        run_row(run_id).get("calls") == "5",
        str(run_row(run_id)),
    )
    tail = list(sse_events(admin, run_id, last_event_id="12"))
    check(
        "Last-Event-ID replays only what follows",
        [e["seq"] for e in tail] == list(range(13, len(SECTION_32) + 1)),
        str([e["seq"] for e in tail]),
    )

    print("2. executor (analytics-orchestrator) killed mid-run -> restart -> resumes")
    run_id = post_message(admin, conversation_id).json()["run_id"]
    check(
        "run reached the SQL stage",
        wait_for(lambda: "sql.started" in db_events(run_id)),
        str(db_events(run_id)),
    )
    before = run_row(run_id)
    orchestrator.kill()
    check("killed while running", before.get("status") == "running", str(before))
    orchestrator.start()
    check(
        "run completes after the restart",
        wait_for(lambda: run_row(run_id).get("status") == "completed", 90),
        str(run_row(run_id)),
    )
    check("events exactly once, in order", db_events(run_id) == SECTION_32, str(db_events(run_id)))
    after = run_row(run_id)
    check(
        "persisted stages were not re-run (5 charged model calls in total)",
        after.get("calls") == "5",
        f"before={before} after={after}",
    )
    resumed_log = (LOG_DIR / f"analytics-orchestrator.{orchestrator.starts}.log").read_text(
        errors="replace"
    )
    resumed = [
        json.loads(line)["context"]["completed_steps"]
        for line in resumed_log.splitlines()
        if '"run execution started"' in line and run_id in line
    ]
    check(
        "restarted executor resumed from persisted steps (completed_steps > 0)",
        bool(resumed) and resumed[0] > 0,
        str(resumed),
    )

    print("3. worker killed mid-run -> restart -> redelivery finds the run, no duplicate execution")
    run_id = post_message(admin, conversation_id).json()["run_id"]
    check(
        "run started",
        wait_for(lambda: "intent.started" in db_events(run_id)),
        str(db_events(run_id)),
    )
    worker.kill()
    worker.start()
    check(
        "run completes",
        wait_for(lambda: run_row(run_id).get("status") == "completed", 90),
        str(run_row(run_id)),
    )
    check("events exactly once, in order", db_events(run_id) == SECTION_32, str(db_events(run_id)))
    check("5 charged model calls", run_row(run_id).get("calls") == "5", str(run_row(run_id)))

    print("4. token budget: a run over its cap fails with RUN_BUDGET_EXCEEDED")
    orchestrator.restart(ANALYTICS_RUN_TOKEN_BUDGET="1000")  # noqa: S106 - a token count
    run_id = post_message(admin, conversation_id).json()["run_id"]
    names = [f"{e['stage']}.{e['status']}" for e in sse_events(admin, run_id)]
    check(
        "SSE shows intent.failed then run.failed",
        names == ["intent.started", "intent.failed", "run.failed"],
        str(names),
    )
    row = run_row(run_id)
    check(
        "run failed RUN_BUDGET_EXCEEDED before any model call",
        row.get("status") == "failed"
        and row.get("error_code") == "RUN_BUDGET_EXCEEDED"
        and row.get("calls") == "0",
        str(row),
    )

    print("secret hygiene")
    logs = {p.name: p.read_text(errors="replace") for p in LOG_DIR.glob("*.log")}
    check(
        "no service log contains the data source credential",
        not [name for name, text in logs.items() if READER_PASSWORD in text],
    )

    if failures:
        print(f"\nFAILED ({len(failures)}): " + "; ".join(failures))
        return 1
    print("\nPhase A5 DoD flow: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
