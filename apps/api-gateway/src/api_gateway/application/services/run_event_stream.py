"""`GET /runs/{id}/events` -- Server-Sent Events for one analytics run (Sections 11, 18).

Any gateway replica can serve any run: it subscribes to Redis channel `analytics:run:{run_id}`
*first*, then replays persisted events from analytics-orchestrator after the client's
`Last-Event-ID`, then streams live events. Events are forwarded in `seq` order exactly once; a gap
(a pub/sub message lost during a Redis blip) is filled from the persisted record, and so is a quiet
stream on every other heartbeat. The stream ends after the run's terminal `run.*` event, or at the
stream time limit (the client reconnects with `Last-Event-ID`).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from pydantic import ValidationError
from redis.asyncio import Redis

from api_gateway.infrastructure.http.run_events_client import RunEventsClient
from platform_contracts import AnalyticsRunEvent

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def run_channel(run_id: uuid.UUID) -> str:
    return f"analytics:run:{run_id}"


def sse_frame(event: AnalyticsRunEvent) -> bytes:
    data = json.dumps(event.to_wire(), separators=(",", ":"))
    return f"id: {event.seq}\nevent: {event.event_name}\ndata: {data}\n\n".encode()


def parse_last_event_id(value: str | None) -> int:
    return int(value) if value and value.isdigit() and len(value) <= 9 else 0


def _events(items: list[dict[str, Any]]) -> list[AnalyticsRunEvent]:
    parsed = []
    for item in items:
        try:
            parsed.append(AnalyticsRunEvent.model_validate(item))
        except ValidationError:
            continue
    return sorted(parsed, key=lambda e: e.seq)


class RunEventStream:
    """Prepared before the response starts, so authorization and 404 still get an error envelope."""

    def __init__(
        self,
        *,
        redis: Redis,
        client: RunEventsClient,
        tenant_id: str,
        run_id: uuid.UUID,
        last_seq: int,
        heartbeat_seconds: float,
        max_seconds: float,
    ) -> None:
        self._redis = redis
        self._client = client
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._last = last_seq
        self._heartbeat = heartbeat_seconds
        self._max = max_seconds
        self._pubsub: Any = None
        self._status = ""
        self._backlog: list[AnalyticsRunEvent] = []

    async def open(self) -> None:
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(run_channel(self._run_id))
        try:
            self._status, items = await self._client.replay(
                self._tenant_id, self._run_id, self._last
            )
        except BaseException:
            await self.close()
            raise
        self._backlog = _events(items)

    async def close(self) -> None:
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe()
                await self._pubsub.aclose()
            except Exception:  # noqa: S110 - closing a subscription we are abandoning
                pass
            self._pubsub = None

    def _take(self, events: list[AnalyticsRunEvent]) -> tuple[list[bytes], bool]:
        frames: list[bytes] = []
        for event in events:
            if event.run_id != str(self._run_id) or event.seq <= self._last:
                continue
            frames.append(sse_frame(event))
            self._last = event.seq
            if event.is_terminal:
                return frames, True
        return frames, False

    async def _resync(self) -> tuple[list[bytes], bool]:
        self._status, items = await self._client.replay(self._tenant_id, self._run_id, self._last)
        frames, done = self._take(_events(items))
        return frames, done or (self._status in TERMINAL_STATUSES and not items)

    async def frames(self) -> AsyncIterator[bytes]:
        try:
            frames, done = self._take(self._backlog)
            for frame in frames:
                yield frame
            if done or (self._status in TERMINAL_STATUSES and not self._backlog):
                return
            deadline = time.monotonic() + self._max
            quiet = 0
            while time.monotonic() < deadline:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=self._heartbeat
                )
                if message is None:
                    yield b": keep-alive\n\n"
                    quiet += 1
                    if quiet % 2 == 0:
                        frames, done = await self._resync()
                        for frame in frames:
                            yield frame
                        if done:
                            return
                    continue
                quiet = 0
                try:
                    event = AnalyticsRunEvent.model_validate_json(message["data"])
                except (ValidationError, TypeError, ValueError):
                    continue
                if event.seq > self._last + 1:
                    frames, done = await self._resync()
                else:
                    frames, done = self._take([event])
                for frame in frames:
                    yield frame
                if done:
                    return
        finally:
            await self.close()
