"""Postgres execution (Section 13): the database-side half of defense in depth.

The validator already allowed only a single, catalog-bound SELECT. Independently of it:

* **one prepared statement** -- the extended protocol refuses multiple commands;
* **read-only REPEATABLE READ transaction**, `default_transaction_read_only=on` at connect;
* **empty `search_path`** -- only `pg_catalog` resolves implicitly, so an unqualified name
  cannot be captured by an object in a writable schema (the validator qualifies every table);
* **server-side `statement_timeout`** set per transaction, plus a client-side timeout;
* **row and byte caps** via a server-side cursor -- rows past the cap are never fetched, and
  truncation is always flagged;
* **Section 15 egress** -- pools connect to the addresses validated at pool creation;
* the credential is expected to be a **read-only database principal** (Section 13), which the
  database itself enforces.

Pools are keyed by data source and credential fingerprint, so a credential rotation opens a
fresh pool, and idle pools are closed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Final

import asyncpg

from platform_egress import EgressPolicy, HostResolutionError, resolve_host
from query_gateway.domain.value_objects.execution import (
    ConnectionCredentials,
    ExecutionError,
    ExecutionFailure,
    ExecutionLimits,
    QueryResult,
    ResultColumn,
)
from query_gateway.infrastructure.connectors.result_values import (
    credential_fingerprint,
    json_value,
)

logger = logging.getLogger(__name__)

APPLICATION_NAME: Final = "buvi-query-gateway"
_FETCH_CHUNK: Final = 500


def classify(exc: BaseException) -> ExecutionError:
    """Map a driver failure to a sanitized code. The message is discarded."""
    if isinstance(exc, ExecutionError):
        return exc
    sqlstate = getattr(exc, "sqlstate", None)
    sqlstate_class = str(sqlstate)[:2] if sqlstate else None
    if isinstance(exc, asyncpg.QueryCanceledError | TimeoutError):
        return ExecutionError(ExecutionFailure.TIMEOUT, sqlstate_class)
    if isinstance(exc, asyncpg.InvalidAuthorizationSpecificationError):
        return ExecutionError(ExecutionFailure.AUTHENTICATION_FAILED, sqlstate_class)
    if isinstance(
        exc,
        asyncpg.InvalidCatalogNameError
        | asyncpg.CannotConnectNowError
        | asyncpg.TooManyConnectionsError,
    ):
        return ExecutionError(ExecutionFailure.UNAVAILABLE, sqlstate_class)
    if isinstance(exc, asyncpg.ReadOnlySQLTransactionError | asyncpg.InsufficientPrivilegeError):
        return ExecutionError(ExecutionFailure.REJECTED_BY_DATABASE, sqlstate_class)
    if isinstance(exc, asyncpg.PostgresError):
        return ExecutionError(ExecutionFailure.QUERY_FAILED, sqlstate_class)
    if isinstance(exc, OSError | asyncpg.InterfaceError | ConnectionError):
        return ExecutionError(ExecutionFailure.UNAVAILABLE, None)
    return ExecutionError(ExecutionFailure.QUERY_FAILED, None)


class PostgresQueryExecutor:
    engine: Final = "postgres"

    def __init__(
        self,
        *,
        egress: EgressPolicy,
        connect_timeout_seconds: float,
        pool_max_size: int,
        pool_idle_seconds: float,
    ) -> None:
        self._egress = egress
        self._connect_timeout = connect_timeout_seconds
        self._pool_max_size = pool_max_size
        self._pool_idle = pool_idle_seconds
        self._pools: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()

    async def _create_pool(self, database_name: str, credentials: ConnectionCredentials) -> Any:
        try:
            addresses = await resolve_host(credentials.host, credentials.port)
        except HostResolutionError:
            raise ExecutionError(ExecutionFailure.UNAVAILABLE) from None
        if not self._egress.permits(credentials.host, addresses):
            raise ExecutionError(ExecutionFailure.DESTINATION_NOT_ALLOWED)
        host = credentials.host if credentials.sslmode == "verify-full" else str(addresses[0])
        try:
            return await asyncpg.create_pool(
                host=host,
                port=credentials.port,
                user=credentials.username,
                password=credentials.password,
                database=database_name,
                ssl=credentials.sslmode,
                timeout=self._connect_timeout,
                min_size=0,
                max_size=self._pool_max_size,
                max_inactive_connection_lifetime=self._pool_idle,
                statement_cache_size=0,
                server_settings={
                    "application_name": APPLICATION_NAME,
                    "default_transaction_read_only": "on",
                    "search_path": "",
                },
            )
        except Exception as exc:
            raise classify(exc) from None

    async def _pool(self, key: str, database_name: str, credentials: ConnectionCredentials) -> Any:
        async with self._lock:
            now = time.monotonic()
            for stale_key, (pool, last_used) in list(self._pools.items()):
                if stale_key != key and now - last_used > self._pool_idle:
                    del self._pools[stale_key]
                    await pool.close()
            entry = self._pools.get(key)
            if entry is None:
                pool = await self._create_pool(database_name, credentials)
            else:
                pool = entry[0]
            self._pools[key] = (pool, now)
            return pool

    async def _drop(self, key: str) -> None:
        async with self._lock:
            entry = self._pools.pop(key, None)
        if entry is not None:
            entry[0].terminate()

    async def execute(
        self,
        *,
        pool_key: str,
        database_name: str,
        credentials: ConnectionCredentials,
        sql: str,
        limits: ExecutionLimits,
    ) -> QueryResult:
        key = f"{pool_key}:{credential_fingerprint(credentials)}"
        pool = await self._pool(key, database_name, credentials)
        client_timeout = limits.timeout_ms / 1000 + 5
        try:
            async with asyncio.timeout(client_timeout):
                return await self._run(pool, sql, limits)
        except ExecutionError:
            raise
        except Exception as exc:
            error = classify(exc)
            if error.failure in (
                ExecutionFailure.UNAVAILABLE,
                ExecutionFailure.AUTHENTICATION_FAILED,
            ):
                await self._drop(key)
            logger.warning(
                "query execution failed",
                extra={
                    "context": {
                        "code": error.failure.value,
                        "sqlstate_class": error.sqlstate_class,
                        "error_type": type(exc).__name__,
                    }
                },
            )
            raise error from None

    async def _run(self, pool: Any, sql: str, limits: ExecutionLimits) -> QueryResult:
        async with (
            pool.acquire(timeout=self._connect_timeout) as connection,
            connection.transaction(readonly=True, isolation="repeatable_read"),
        ):
            await connection.execute(
                "SELECT set_config('statement_timeout', $1, true)", str(limits.timeout_ms)
            )
            statement = await connection.prepare(sql)
            columns = tuple(
                ResultColumn(name=attribute.name, type=attribute.type.name)
                for attribute in statement.get_attributes()
            )
            cursor = await statement.cursor()
            rows: list[list[Any]] = []
            size = 2
            truncated_by: str | None = None
            while truncated_by is None:
                batch = await cursor.fetch(min(_FETCH_CHUNK, limits.max_rows + 1 - len(rows)))
                if not batch:
                    break
                for record in batch:
                    if len(rows) == limits.max_rows:
                        truncated_by = "rows"
                        break
                    row = [json_value(value) for value in record]
                    row_bytes = len(json.dumps(row, separators=(",", ":"))) + 1
                    if size + row_bytes > limits.max_bytes:
                        truncated_by = "bytes"
                        break
                    rows.append(row)
                    size += row_bytes
        return QueryResult(
            columns=columns,
            rows=rows,
            truncated=truncated_by is not None,
            truncation_reason=truncated_by,
            bytes_returned=size,
        )

    async def close(self) -> None:
        async with self._lock:
            pools = [pool for pool, _ in self._pools.values()]
            self._pools.clear()
        for pool in pools:
            await pool.close()
