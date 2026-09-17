"""`GET /runs/{id}/events` (Sections 11, 18, 32): replay then live, exactly once, in order."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis

from api_gateway.core.config import Settings
from api_gateway.tests.conftest import FakeUpstreams, build_client

pytestmark = pytest.mark.integration

SEQUENCE = [
    ("intent", "started"),
    ("intent", "completed"),
    ("schema", "started"),
    ("schema", "completed"),
    ("sql", "started"),
    ("sql", "completed"),
    ("validation", "started"),
    ("validation", "completed"),
    ("execution", "started"),
    ("execution", "completed"),
    ("visualization", "started"),
    ("visualization", "completed"),
    ("artifact", "started"),
    ("artifact", "completed"),
    ("run", "completed"),
]


def _event(run_id: uuid.UUID, seq: int) -> dict[str, Any]:
    stage, status = SEQUENCE[seq - 1]
    body: dict[str, Any] = {
        "runId": str(run_id),
        "seq": seq,
        "stage": stage,
        "status": status,
        "message": f"{stage} {status}",
        "createdAt": dt.datetime.now(dt.UTC).isoformat(),
    }
    if (stage, status) == ("artifact", "completed"):
        body["artifactId"] = str(uuid.uuid4())
    return body


def _frames(text: str) -> list[dict[str, str]]:
    frames = []
    for block in text.split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if line and not line.startswith(":")
        )
        if fields:
            frames.append(fields)
    return frames


class Orchestrator:
    def __init__(self, upstreams: FakeUpstreams) -> None:
        self.runs: dict[str, tuple[str, str, list[dict[str, Any]]]] = {}
        self.requests: list[httpx.Request] = []
        original = upstreams.handler

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/internal/v1/runs/") and request.url.path.endswith(
                "/events"
            ):
                self.requests.append(request)
                run_id = request.url.path.split("/")[4]
                entry = self.runs.get(run_id)
                if entry is None or entry[0] != request.url.params["tenant_id"]:
                    return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
                after = int(request.url.params["after_seq"])
                return httpx.Response(
                    200,
                    json={
                        "run_id": run_id,
                        "status": entry[1],
                        "events": [e for e in entry[2] if e["seq"] > after],
                    },
                )
            return original(request)

        upstreams.handler = handler  # type: ignore[method-assign]


@pytest.fixture
def orchestrator(upstreams: FakeUpstreams) -> Orchestrator:
    return Orchestrator(upstreams)


async def test_stream_replays_then_streams_live_until_run_completed(
    settings: Settings, upstreams: FakeUpstreams, orchestrator: Orchestrator, redis_url: str
) -> None:
    run_id = uuid.uuid4()
    tenant = upstreams.principals["client"]["tenant_id"]
    events = [_event(run_id, seq) for seq in range(1, 16)]
    orchestrator.runs[str(run_id)] = (tenant, "running", events[:4])
    publisher = aioredis.Redis.from_url(redis_url)

    async def publish_rest() -> None:
        await asyncio.sleep(0.3)
        await publisher.publish(
            f"analytics:run:{run_id}", json.dumps(events[2])
        )  # duplicate, dropped
        for event in events[4:]:
            await publisher.publish(f"analytics:run:{run_id}", json.dumps(event))
            await asyncio.sleep(0.01)

    fast = settings.model_copy(update={"sse_heartbeat_seconds": 0.2})
    async for _, client in build_client(fast, upstreams):
        task = asyncio.create_task(publish_rest())
        response = await client.get(
            f"/api/v1/runs/{run_id}/events", cookies={"buvi_session": "client"}
        )
        await task
    await publisher.aclose()
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _frames(response.text)
    assert [f["event"] for f in frames] == [f"{s}.{t}" for s, t in SEQUENCE]
    assert [int(f["id"]) for f in frames] == list(range(1, 16))
    payloads = [json.loads(f["data"]) for f in frames]
    assert payloads[13]["artifactId"] and all(p["runId"] == str(run_id) for p in payloads)
    assert orchestrator.requests[0].url.params["tenant_id"] == tenant


async def test_last_event_id_resumes_and_a_gap_is_filled_from_the_record(
    settings: Settings, upstreams: FakeUpstreams, orchestrator: Orchestrator, redis_url: str
) -> None:
    run_id = uuid.uuid4()
    tenant = upstreams.principals["client"]["tenant_id"]
    events = [_event(run_id, seq) for seq in range(1, 16)]
    orchestrator.runs[str(run_id)] = (tenant, "running", events[:10])
    publisher = aioredis.Redis.from_url(redis_url)

    async def publish_after_gap() -> None:
        await asyncio.sleep(0.3)
        orchestrator.runs[str(run_id)] = (tenant, "completed", events)
        await publisher.publish(
            f"analytics:run:{run_id}", json.dumps(events[14])
        )  # seq 15 arrives; 11-14 were lost

    fast = settings.model_copy(update={"sse_heartbeat_seconds": 0.2})
    async for _, client in build_client(fast, upstreams):
        task = asyncio.create_task(publish_after_gap())
        response = await client.get(
            f"/api/v1/runs/{run_id}/events",
            cookies={"buvi_session": "client"},
            headers={"Last-Event-ID": "7"},
        )
        await task
    await publisher.aclose()
    ids = [int(f["id"]) for f in _frames(response.text)]
    assert ids == list(range(8, 16))


async def test_finished_run_replays_and_closes_immediately(
    orchestrator: Orchestrator, upstreams: FakeUpstreams, client: httpx.AsyncClient
) -> None:
    run_id = uuid.uuid4()
    tenant = upstreams.principals["client"]["tenant_id"]
    orchestrator.runs[str(run_id)] = (
        tenant,
        "completed",
        [_event(run_id, seq) for seq in range(1, 16)],
    )
    response = await client.get(f"/api/v1/runs/{run_id}/events", cookies={"buvi_session": "client"})
    assert len(_frames(response.text)) == 15
    again = await client.get(
        f"/api/v1/runs/{run_id}/events",
        cookies={"buvi_session": "client"},
        headers={"Last-Event-ID": "15"},
    )
    assert again.status_code == 200 and _frames(again.text) == []


async def test_stream_authorization(
    orchestrator: Orchestrator, upstreams: FakeUpstreams, client: httpx.AsyncClient
) -> None:
    run_id = uuid.uuid4()
    orchestrator.runs[str(run_id)] = (upstreams.principals["u3"]["tenant_id"], "running", [])
    foreign = await client.get(f"/api/v1/runs/{run_id}/events", cookies={"buvi_session": "client"})
    assert foreign.status_code == 404 and foreign.json()["error"]["code"] == "NOT_FOUND"
    assert (await client.get(f"/api/v1/runs/{run_id}/events")).status_code == 401
    assert (
        await client.get(f"/api/v1/runs/{run_id}/events", cookies={"buvi_session": "nobody"})
    ).status_code == 403
    assert (
        await client.get("/api/v1/runs/not-a-uuid/events", cookies={"buvi_session": "client"})
    ).status_code == 404
