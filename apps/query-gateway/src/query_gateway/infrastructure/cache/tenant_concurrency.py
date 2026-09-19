"""Per-tenant query concurrency (Sections 20, 24): one noisy tenant cannot starve the others.

Over the cap a query is refused at once (`429 QUERY_CONCURRENCY_LIMITED`, with Retry-After),
never queued behind the offender.

* **Across replicas** (Phase A10): each running query holds a lease in a Redis sorted set per
  tenant, scored by its expiry. One Lua script prunes expired leases, counts and takes a slot
  atomically, using Redis's own clock. A replica that dies mid-query leaks nothing for longer
  than the lease, which outlasts the longest allowed query.
* **Never unbounded:** if Redis is unreachable, the per-process limit applies until it is
  back. A tenant can then run up to one cap per replica, not an unlimited number.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from redis.asyncio import Redis
from redis.exceptions import RedisError

from query_gateway.domain.errors import QueryConcurrencyLimitedError

logger = logging.getLogger(__name__)

RETRY_AFTER_SECONDS: Final = 2

_ACQUIRE: Final = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
  return 0
end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[3])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]))
return 1
"""


def _limited() -> QueryConcurrencyLimitedError:
    return QueryConcurrencyLimitedError(headers={"Retry-After": str(RETRY_AFTER_SECONDS)})


class TenantConcurrencyLimiter:
    """Per process: the fallback, and the whole limit where no Redis is configured."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._active: dict[uuid.UUID, int] = {}

    @asynccontextmanager
    async def slot(self, tenant_id: uuid.UUID) -> AsyncIterator[None]:
        if self._active.get(tenant_id, 0) >= self._limit:
            raise _limited()
        self._active[tenant_id] = self._active.get(tenant_id, 0) + 1
        try:
            yield
        finally:
            remaining = self._active[tenant_id] - 1
            if remaining:
                self._active[tenant_id] = remaining
            else:
                del self._active[tenant_id]

    def active(self, tenant_id: uuid.UUID) -> int:
        return self._active.get(tenant_id, 0)


class RedisTenantConcurrencyLimiter:
    def __init__(self, redis: Redis, *, limit: int, lease_seconds: float) -> None:
        self._redis = redis
        self._limit = limit
        self._lease_ms = int(lease_seconds * 1000)
        self._acquire = redis.register_script(_ACQUIRE)
        self._fallback = TenantConcurrencyLimiter(limit)

    @asynccontextmanager
    async def slot(self, tenant_id: uuid.UUID) -> AsyncIterator[None]:
        key = f"qg:inflight:{tenant_id}"
        lease = uuid.uuid4().hex
        try:
            taken = int(await self._acquire(keys=[key], args=[self._limit, self._lease_ms, lease]))
        except RedisError:
            logger.warning("query concurrency store unavailable; per-process limit applies")
            async with self._fallback.slot(tenant_id):
                yield
            return
        if not taken:
            raise _limited()
        try:
            yield
        finally:
            try:
                await self._redis.zrem(key, lease)
            except RedisError:
                logger.warning("query concurrency lease not released; it expires on its own")

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except RedisError:
            return False
