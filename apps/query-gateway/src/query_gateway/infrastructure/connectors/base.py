"""The execution connector contract (Sections 4.1, 13). Engines plug in behind it (Phase A8)."""

from __future__ import annotations

from typing import Protocol

from query_gateway.domain.value_objects.execution import (
    ConnectionCredentials,
    ExecutionLimits,
    QueryResult,
)


class QueryExecutor(Protocol):
    engine: str

    async def execute(
        self,
        *,
        pool_key: str,
        database_name: str,
        credentials: ConnectionCredentials,
        sql: str,
        limits: ExecutionLimits,
    ) -> QueryResult:
        """Run one validated statement read-only. Raises `ExecutionError` on failure."""
        ...

    async def close(self) -> None: ...
