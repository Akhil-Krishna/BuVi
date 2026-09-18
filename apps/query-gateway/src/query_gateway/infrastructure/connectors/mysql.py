"""MySQL 8 execution (Section 13, Phase A8): the database-side half of defense in depth.

The validator already allowed only one catalog-bound SELECT, regenerated in MySQL syntax without
comments. Independently of it, every session here:

* runs with the **multi-statement protocol flag off** and **`LOCAL INFILE` off** -- the server
  refuses a second statement and cannot ask the client for a file. aiomysql requests
  `CLIENT.MULTI_STATEMENTS` on every connection it makes, so connections are built here with the
  flag cleared before the handshake (and pooled here, since aiomysql's pool builds its own);
* is **read-only** (`transaction_read_only = ON` for the session at connect, and each query
  inside `START TRANSACTION READ ONLY`);
* has a **pinned `sql_mode`** -- MySQL 8's default, without `ANSI_QUOTES` or
  `NO_BACKSLASH_ESCAPES`. That is the syntax the validator parsed and regenerated; a server
  configured otherwise must not reinterpret quotes or backslashes (a parser differential);
* sets a server-side **`max_execution_time`** per query, plus a client-side timeout;
* streams with an unbuffered cursor and stops at the **row and byte caps**. A truncated
  connection is discarded rather than drained;
* connects only to addresses that pass **Section 15 egress** at pool creation;
* expects a **SELECT-only database user**, which MySQL itself enforces.

Driver messages never leave this module: failures become an `ExecutionError` code.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

import aiomysql
import pymysql
from pymysql.constants import CLIENT, FIELD_TYPE

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

#: MySQL 8's default sql_mode, pinned: no ANSI_QUOTES, no NO_BACKSLASH_ESCAPES (see above).
SQL_MODE: Final = (
    "ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,"
    "ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
)
SESSION_INIT: Final = f"SET SESSION sql_mode = '{SQL_MODE}', SESSION transaction_read_only = ON"
_FETCH_CHUNK: Final = 500

_TYPE_NAMES: Final[dict[int, str]] = {
    FIELD_TYPE.DECIMAL: "decimal",
    FIELD_TYPE.NEWDECIMAL: "decimal",
    FIELD_TYPE.TINY: "integer",
    FIELD_TYPE.SHORT: "integer",
    FIELD_TYPE.LONG: "integer",
    FIELD_TYPE.INT24: "integer",
    FIELD_TYPE.LONGLONG: "bigint",
    FIELD_TYPE.YEAR: "integer",
    FIELD_TYPE.FLOAT: "double",
    FIELD_TYPE.DOUBLE: "double",
    FIELD_TYPE.DATE: "date",
    FIELD_TYPE.NEWDATE: "date",
    FIELD_TYPE.DATETIME: "timestamp",
    FIELD_TYPE.TIMESTAMP: "timestamp",
    FIELD_TYPE.TIME: "time",
    FIELD_TYPE.JSON: "json",
}

#: MySQL error numbers -> sanitized failure.
_TIMEOUT: Final = frozenset({3024})  # ER_QUERY_TIMEOUT (max_execution_time)
_AUTH: Final = frozenset({1045, 1698})
_UNAVAILABLE: Final = frozenset({1040, 1049, 1053, 2002, 2003, 2006, 2013})
_REJECTED: Final = frozenset({1044, 1142, 1143, 1227, 1792})  # denied / read-only transaction


def mysql_ssl(sslmode: str) -> ssl.SSLContext | None:
    if sslmode == "disable":
        return None
    context = ssl.create_default_context()
    if sslmode == "require":  # encrypted, not verified -- same meaning as libpq's `require`
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


class SingleStatementConnection(aiomysql.Connection):  # type: ignore[misc]
    """An aiomysql connection that does *not* ask for multi-statement support: the server then
    rejects `SELECT 1; DROP ...` at the protocol level, whatever the validator did."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.client_flag &= ~CLIENT.MULTI_STATEMENTS


async def open_single_statement_connection(**kwargs: Any) -> Any:
    connection = SingleStatementConnection(**kwargs)
    await connection._connect()  # what aiomysql.connect() does after building its Connection
    return connection


class ConnectionPool:
    """A bounded pool of single-statement connections for one data source and credential."""

    def __init__(self, connect_kwargs: dict[str, Any], max_size: int) -> None:
        self._kwargs = connect_kwargs
        self._slots = asyncio.Semaphore(max_size)
        self._idle: list[Any] = []

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        async with self._slots:
            connection = None
            while self._idle and connection is None:
                candidate = self._idle.pop()
                connection = None if candidate.closed else candidate
            if connection is None:
                connection = await open_single_statement_connection(**self._kwargs)
            try:
                yield connection
            finally:
                if not connection.closed:
                    self._idle.append(connection)

    def close(self) -> None:
        while self._idle:
            self._idle.pop().close()


def classify(exc: BaseException) -> ExecutionError:
    """Map a driver failure to a sanitized code. The message is discarded."""
    if isinstance(exc, ExecutionError):
        return exc
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return ExecutionError(ExecutionFailure.TIMEOUT)
    code = exc.args[0] if isinstance(exc, pymysql.MySQLError) and exc.args else None
    if code in _TIMEOUT:
        return ExecutionError(ExecutionFailure.TIMEOUT)
    if code in _AUTH:
        return ExecutionError(ExecutionFailure.AUTHENTICATION_FAILED)
    if code in _UNAVAILABLE:
        return ExecutionError(ExecutionFailure.UNAVAILABLE)
    if code in _REJECTED:
        return ExecutionError(ExecutionFailure.REJECTED_BY_DATABASE)
    if isinstance(exc, pymysql.MySQLError):
        return ExecutionError(ExecutionFailure.QUERY_FAILED)
    if isinstance(exc, OSError | ConnectionError):
        return ExecutionError(ExecutionFailure.UNAVAILABLE)
    return ExecutionError(ExecutionFailure.QUERY_FAILED)


class MySqlQueryExecutor:
    engine: Final = "mysql"

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
        return ConnectionPool(
            {
                "host": host,
                "port": credentials.port,
                "user": credentials.username,
                "password": credentials.password,
                "db": database_name,
                "ssl": mysql_ssl(credentials.sslmode),
                "connect_timeout": self._connect_timeout,
                "charset": "utf8mb4",
                # aiomysql's own sql_mode= sends the value unquoted (a syntax error for a list),
                # so the pinned mode and the read-only session are one statement we control.
                "init_command": SESSION_INIT,
                "local_infile": False,
                "autocommit": True,
                "program_name": "buvi-query-gateway",
            },
            self._pool_max_size,
        )

    async def _pool(self, key: str, database_name: str, credentials: ConnectionCredentials) -> Any:
        async with self._lock:
            now = time.monotonic()
            for stale_key, (pool, last_used) in list(self._pools.items()):
                if stale_key != key and now - last_used > self._pool_idle:
                    del self._pools[stale_key]
                    pool.close()
            entry = self._pools.get(key)
            pool = entry[0] if entry else await self._create_pool(database_name, credentials)
            self._pools[key] = (pool, now)
            return pool

    async def _drop(self, key: str) -> None:
        async with self._lock:
            entry = self._pools.pop(key, None)
        if entry is not None:
            entry[0].close()

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
        try:
            async with asyncio.timeout(limits.timeout_ms / 1000 + 5):
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
                extra={"context": {"code": error.failure.value, "error_type": type(exc).__name__}},
            )
            raise error from None

    async def _run(self, pool: Any, sql: str, limits: ExecutionLimits) -> QueryResult:
        async with pool.acquire() as connection:
            completed = False
            try:
                async with connection.cursor(aiomysql.SSCursor) as cursor:
                    await cursor.execute(
                        f"SET SESSION max_execution_time = {int(limits.timeout_ms)}"
                    )
                    await cursor.execute("START TRANSACTION READ ONLY")
                    # No arguments: the driver does no %-interpolation of the validated text.
                    await cursor.execute(sql)
                    columns = tuple(
                        ResultColumn(name=str(d[0]), type=_TYPE_NAMES.get(d[1], "text"))
                        for d in cursor.description or ()
                    )
                    rows, size, truncated_by = await self._fetch(cursor, limits)
                    if truncated_by is None:
                        await connection.rollback()
                        completed = True
            finally:
                if not completed:
                    # Unread rows or a failed statement: discard, never return it to the pool.
                    connection.close()
        return QueryResult(
            columns=columns,
            rows=rows,
            truncated=truncated_by is not None,
            truncation_reason=truncated_by,
            bytes_returned=size,
        )

    @staticmethod
    async def _fetch(
        cursor: Any, limits: ExecutionLimits
    ) -> tuple[list[list[Any]], int, str | None]:
        rows: list[list[Any]] = []
        size = 2
        while True:
            batch = await cursor.fetchmany(min(_FETCH_CHUNK, limits.max_rows + 1 - len(rows)))
            if not batch:
                return rows, size, None
            for record in batch:
                if len(rows) == limits.max_rows:
                    return rows, size, "rows"
                row = [json_value(value) for value in record]
                row_bytes = len(json.dumps(row, separators=(",", ":"))) + 1
                if size + row_bytes > limits.max_bytes:
                    return rows, size, "bytes"
                rows.append(row)
                size += row_bytes

    async def close(self) -> None:
        async with self._lock:
            pools = [pool for pool, _ in self._pools.values()]
            self._pools.clear()
        for pool in pools:
            pool.close()
