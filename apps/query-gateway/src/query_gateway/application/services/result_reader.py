"""Use case: read the rows behind a stored result handle (Section 13, Phase A6).

Serves `GET /artifacts/{id}/data` through dashboard-service. Only this service knows its handle
format, so it parses the handle itself, and it answers only for a *succeeded* `analytics_run`
execution of the named tenant whose stored handle is exactly the one presented -- a developer's
SQL editor result is never served here. Past the TTL the answer is `RESULT_EXPIRED`, even if the
object has not been swept yet; nothing is ever re-executed.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Final

from query_gateway.domain.errors import (
    NotFoundError,
    ResultExpiredError,
    ResultStoreUnavailableError,
)
from query_gateway.domain.value_objects.policy import Purpose
from query_gateway.infrastructure.db.repositories.query_repository import QueryRepository
from query_gateway.infrastructure.storage.base import ResultStore, ResultStoreError

_HANDLE: Final = re.compile(
    r"^(?:s3://[a-z0-9.\-]{3,63}/|memory://)tenants/(?P<tenant>[0-9a-f\-]{36})/queries/"
    r"(?P<query>[0-9a-f\-]{36})\.json$"
)


@dataclass(frozen=True)
class StoredRows:
    query_id: uuid.UUID
    columns: list[dict[str, str]]
    rows: list[list[Any]]
    truncated: bool
    expires_at: dt.datetime


class ResultReader:
    def __init__(self, *, repository: QueryRepository, results: ResultStore, ttl_days: int) -> None:
        self._repository = repository
        self._results = results
        self._ttl = dt.timedelta(days=ttl_days)

    async def read(self, tenant_id: uuid.UUID, handle: str) -> StoredRows:
        match = _HANDLE.match(handle)
        if match is None or match["tenant"] != str(tenant_id):
            raise NotFoundError()
        query_id = uuid.UUID(match["query"])
        execution = await self._repository.get(tenant_id, query_id)
        if (
            execution is None
            or execution.result_handle != handle
            or execution.purpose != Purpose.ANALYTICS_RUN.value
            or execution.status != "succeeded"
        ):
            raise NotFoundError()
        expires_at = execution.created_at + self._ttl
        if dt.datetime.now(dt.UTC) >= expires_at:
            raise ResultExpiredError()
        try:
            payload = await self._results.get(tenant_id=tenant_id, query_id=query_id)
        except ResultStoreError:
            raise ResultStoreUnavailableError() from None
        if payload is None:
            raise ResultExpiredError()
        body = json.loads(payload)
        return StoredRows(
            query_id=query_id,
            columns=[{"name": c["name"], "type": c["type"]} for c in body["columns"]],
            rows=body["rows"],
            truncated=bool(body["truncated"]),
            expires_at=expires_at,
        )
