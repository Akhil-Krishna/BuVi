"""Result-handle storage contract (Sections 8.5, 13, 24): customer data at rest only with a TTL."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Protocol


class ResultStoreError(Exception):
    """The result store failed. No detail: bucket and key names stay internal."""


@dataclass(frozen=True)
class StoredResult:
    handle: str
    expires_at: dt.datetime


def result_key(tenant_id: uuid.UUID, query_id: uuid.UUID) -> str:
    return f"tenants/{tenant_id}/queries/{query_id}.json"


class ResultStore(Protocol):
    async def put(
        self, *, tenant_id: uuid.UUID, query_id: uuid.UUID, payload: bytes
    ) -> StoredResult: ...

    async def get(self, *, tenant_id: uuid.UUID, query_id: uuid.UUID) -> bytes | None:
        """The stored payload, or None when the object no longer exists."""
        ...

    async def ping(self) -> bool: ...


class InMemoryResultStore:
    """Tests only; refused outside dev/test."""

    def __init__(self, ttl_days: int = 1) -> None:
        self.objects: dict[str, bytes] = {}
        self._ttl = dt.timedelta(days=ttl_days)

    async def put(
        self, *, tenant_id: uuid.UUID, query_id: uuid.UUID, payload: bytes
    ) -> StoredResult:
        key = result_key(tenant_id, query_id)
        self.objects[key] = payload
        return StoredResult(
            handle=f"memory://{key}", expires_at=dt.datetime.now(dt.UTC) + self._ttl
        )

    async def get(self, *, tenant_id: uuid.UUID, query_id: uuid.UUID) -> bytes | None:
        return self.objects.get(result_key(tenant_id, query_id))

    async def ping(self) -> bool:
        return True
