"""Per-tenant query concurrency across replicas (Section 20; Phase A10), on a real Redis.

Two limiters sharing one Redis stand for two query-gateway replicas: the cap is the tenant's,
not each replica's. A crashed holder's lease expires; a Redis outage falls back to the
per-process cap, never to no cap.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from redis.asyncio import Redis

from query_gateway.domain.errors import QueryConcurrencyLimitedError
from query_gateway.infrastructure.cache.tenant_concurrency import RedisTenantConcurrencyLimiter

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def redis_url() -> Iterator[str]:
    import redis
    from testcontainers.core.container import DockerContainer

    with DockerContainer("redis:7").with_exposed_ports(6379) as container:
        url = f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"
        deadline = time.monotonic() + 30
        while True:
            try:
                if redis.Redis.from_url(url).ping():
                    break
            except redis.RedisError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        yield url


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.aclose()


async def test_the_cap_is_per_tenant_across_replicas(redis: Redis) -> None:
    one = RedisTenantConcurrencyLimiter(redis, limit=2, lease_seconds=30)
    two = RedisTenantConcurrencyLimiter(redis, limit=2, lease_seconds=30)
    tenant, other = uuid.uuid4(), uuid.uuid4()
    async with one.slot(tenant), two.slot(tenant):
        for replica in (one, two):
            with pytest.raises(QueryConcurrencyLimitedError) as info:
                async with replica.slot(tenant):
                    pass
            assert info.value.headers["Retry-After"] == "2"
        async with one.slot(other):  # another tenant is unaffected
            pass
    async with two.slot(tenant), one.slot(tenant):  # released on exit, even across replicas
        pass


async def test_a_crashed_holders_lease_expires(redis: Redis) -> None:
    limiter = RedisTenantConcurrencyLimiter(redis, limit=1, lease_seconds=0.3)
    tenant = uuid.uuid4()
    held = limiter.slot(tenant)
    await held.__aenter__()  # a replica takes the slot, then dies without releasing it
    with pytest.raises(QueryConcurrencyLimitedError):
        async with limiter.slot(tenant):
            pass
    await asyncio.sleep(0.5)
    async with limiter.slot(tenant):
        pass


async def test_a_redis_outage_falls_back_to_the_per_process_cap() -> None:
    dead = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    limiter = RedisTenantConcurrencyLimiter(dead, limit=1, lease_seconds=30)
    tenant = uuid.uuid4()
    try:
        async with limiter.slot(tenant):
            with pytest.raises(QueryConcurrencyLimitedError):
                async with limiter.slot(tenant):
                    pass
        async with limiter.slot(tenant):
            pass
    finally:
        await dead.aclose()
