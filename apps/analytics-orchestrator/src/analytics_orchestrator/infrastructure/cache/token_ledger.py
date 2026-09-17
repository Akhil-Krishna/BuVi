"""Per-tenant daily token usage (Section 23), in Redis: `llm:tokens:{tenant}:{YYYYMMDD}`.

Counters expire after two days. The per-run cap is durable in `flow_state.usage`; this daily
counter is an enforcement aid, and the router fails closed if it cannot be read.
"""

from __future__ import annotations

import datetime as dt

from redis.asyncio import Redis

_TTL_SECONDS = 2 * 24 * 3600


def _key(tenant_id: str) -> str:
    return f"llm:tokens:{tenant_id}:{dt.datetime.now(dt.UTC):%Y%m%d}"


class RedisTokenLedger:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def used_today(self, tenant_id: str) -> int:
        value = await self._redis.get(_key(tenant_id))
        return int(value) if value else 0

    async def charge(self, tenant_id: str, tokens: int) -> None:
        key = _key(tenant_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incrby(key, tokens)
            pipe.expire(key, _TTL_SECONDS)
            await pipe.execute()
