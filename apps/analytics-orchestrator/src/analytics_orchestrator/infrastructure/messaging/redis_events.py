"""Redis pub/sub fan-out of run events (Sections 11, 18): channel `analytics:run:{run_id}`. Redis
is transport only; `analytics.run_events` is the record SSE replays from."""

from __future__ import annotations

import json

from redis.asyncio import Redis

from platform_contracts import AnalyticsRunEvent


def run_channel(run_id: str) -> str:
    return f"analytics:run:{run_id}"


class RedisRunEventPublisher:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, event: AnalyticsRunEvent) -> None:
        await self._redis.publish(run_channel(event.run_id), json.dumps(event.to_wire()))
