"""Redis token bucket (Sections 5, 20).

One Lua script per bucket makes refill-and-consume atomic, and it reads Redis's own
clock (`TIME`), so gateway replicas with skewed clocks still share one fair count.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Final, Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from api_gateway.domain.policies.rate_limit import Bucket

logger = logging.getLogger(__name__)

TOKEN_BUCKET_LUA: Final = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil then
  tokens = capacity
  ts = now
end
tokens = math.min(capacity, tokens + (math.max(0, now - ts) / 1000.0) * rate)
local allowed = 0
local retry_ms = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry_ms = math.ceil((cost - tokens) / rate * 1000)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity / rate * 1000) + 1000)
return {allowed, retry_ms}
"""  # noqa: S105 - Lua source, not a secret

#: Give back a token this process took from a bucket, in the same request, that a *later*
#: bucket then denied -- a multi-bucket `consume()` must not have a request it ultimately
#: refused permanently spend the budget of the buckets it happened to clear first (a noisy
#: tenant must not silently drain each of its users' own per-user budgets on every request
#: of theirs the *tenant* bucket -- not they -- denies).
TOKEN_BUCKET_REFUND_LUA: Final = """
local capacity = tonumber(ARGV[1])
local cost = tonumber(ARGV[2])
local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens'))
if tokens == nil then
  return 0
end
redis.call('HSET', KEYS[1], 'tokens', tostring(math.min(capacity, tokens + cost)))
return 1
"""  # noqa: S105 - Lua source, not a secret


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after_seconds: int = 0
    scope: str | None = None


class RateLimiter(Protocol):
    async def consume(self, buckets: list[Bucket]) -> Decision: ...
    async def ping(self) -> bool: ...


class RedisRateLimiter:
    def __init__(self, redis: Redis, *, fail_open: bool) -> None:
        self._redis = redis
        self._script = redis.register_script(TOKEN_BUCKET_LUA)
        self._refund_script = redis.register_script(TOKEN_BUCKET_REFUND_LUA)
        self._fail_open = fail_open

    async def consume(self, buckets: list[Bucket]) -> Decision:
        """Take one token from each bucket in order; deny on the first empty one.

        A request denied partway through has already spent a token in every bucket that
        cleared before the one that refused it -- those are refunded, so a bucket further
        down the chain (e.g. the shared tenant bucket) can never permanently drain a bucket
        that comes before it (e.g. one user's own budget) on a request that bucket did not
        itself deny.
        """
        taken: list[Bucket] = []
        try:
            for bucket in buckets:
                allowed, retry_ms = await self._script(
                    keys=[bucket.key],
                    args=[bucket.rule.capacity, bucket.rule.refill_per_second, 1],
                )
                if int(allowed) != 1:
                    await self._refund(taken)
                    return Decision(
                        allowed=False,
                        retry_after_seconds=max(1, math.ceil(int(retry_ms) / 1000)),
                        scope=bucket.rule.scope,
                    )
                taken.append(bucket)
        except RedisError as exc:
            logger.warning(
                "rate limiter unavailable",
                extra={"context": {"fail_open": self._fail_open, "error": type(exc).__name__}},
            )
            return Decision(allowed=self._fail_open, retry_after_seconds=1, scope="unavailable")
        return Decision(allowed=True)

    async def _refund(self, buckets: list[Bucket]) -> None:
        """Best effort: a lost refund only costs a little of the bucket's own future budget,
        the same way a lost lease would -- it never turns into an incorrect *allow*."""
        for bucket in buckets:
            try:
                await self._refund_script(keys=[bucket.key], args=[bucket.rule.capacity, 1])
            except RedisError:
                logger.warning(
                    "rate limit refund failed",
                    extra={"context": {"bucket": bucket.rule.name}},
                )

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except RedisError:
            return False
