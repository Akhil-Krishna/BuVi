"""Per-tenant query concurrency (Sections 20, 24): one noisy tenant cannot starve the others.

Per process: each replica enforces the cap for the queries it runs. A cluster-wide cap
(Redis leases) is a Track C hardening item (ADR 0005). Over the cap a query is refused
immediately (429), never queued behind the offender.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from query_gateway.domain.errors import QueryConcurrencyLimitedError


class TenantConcurrencyLimiter:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._active: dict[uuid.UUID, int] = {}

    @asynccontextmanager
    async def slot(self, tenant_id: uuid.UUID) -> AsyncIterator[None]:
        if self._active.get(tenant_id, 0) >= self._limit:
            raise QueryConcurrencyLimitedError()
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
