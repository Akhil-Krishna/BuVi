"""The Postgres connector (Phase A3: "one Postgres connector"; Sections 13.1, 15, 20).

Defense in depth around a connection to a customer database:

* **Egress (Section 15).** The host is resolved at connect time, every address must
  pass `EgressPolicy`, and the driver connects to those addresses -- not the name.
  `verify-full` connects by name so the certificate can be verified; a rebound name
  then fails TLS verification before any credential is sent.
* **Read-only, bounded.** `default_transaction_read_only=on`, a server-side
  `statement_timeout`, a connect timeout, and caps on catalog size. Introspection runs
  in one read-only REPEATABLE READ transaction, so tables, columns and foreign keys
  are one consistent view.
* **Catalog-only SQL.** Fixed `pg_catalog` queries with bound parameters. Only objects
  the connected role can actually `SELECT` are returned -- the catalog shows what the
  platform's read principal can reach, not everything that exists.
* **No driver text escapes.** Exceptions are classified into a `DiagnosticCode` and
  logged by type only; the driver message (which names host, port and user) is dropped.
"""

from __future__ import annotations

import logging
import socket
import ssl
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Final

import asyncpg

from metadata_service.domain.value_objects.catalog import (
    IntrospectedCatalog,
    IntrospectedColumn,
    IntrospectedForeignKey,
    IntrospectedTable,
)
from metadata_service.domain.value_objects.connection import ConnectionTarget
from metadata_service.domain.value_objects.diagnostics import (
    ConnectivityResult,
    ConnectorError,
    DiagnosticCode,
    connected_message,
    failure_message,
)
from metadata_service.infrastructure.connectors.base import ConnectorLimits
from metadata_service.infrastructure.connectors.egress import HostResolver, resolve_host
from platform_egress import EgressPolicy

logger = logging.getLogger(__name__)

APPLICATION_NAME: Final = "buvi-metadata-service"

# Relation kinds the catalog exposes: tables, partitioned tables, views, materialized
# views, foreign tables. Partitions are represented by their parent.
COUNT_TABLES_SQL: Final = """
SELECT count(*)
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY($1::text[])
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND NOT c.relispartition
  AND has_schema_privilege(n.oid, 'USAGE')
  AND has_table_privilege(c.oid, 'SELECT')
"""

TABLES_SQL: Final = """
SELECT n.nspname AS schema_name,
       c.relname AS table_name,
       pg_catalog.obj_description(c.oid, 'pg_class') AS description,
       CASE WHEN c.relkind IN ('r', 'm') AND c.reltuples >= 0
            THEN c.reltuples::bigint END AS row_count_estimate
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY($1::text[])
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND NOT c.relispartition
  AND has_schema_privilege(n.oid, 'USAGE')
  AND has_table_privilege(c.oid, 'SELECT')
ORDER BY n.nspname, c.relname
"""

COLUMNS_SQL: Final = """
SELECT n.nspname AS schema_name,
       c.relname AS table_name,
       a.attname AS column_name,
       pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
       pg_catalog.col_description(c.oid, a.attnum) AS description
FROM pg_catalog.pg_attribute a
JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY($1::text[])
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND NOT c.relispartition
  AND has_schema_privilege(n.oid, 'USAGE')
  AND has_table_privilege(c.oid, 'SELECT')
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY n.nspname, c.relname, a.attnum
LIMIT $2
"""

FOREIGN_KEYS_SQL: Final = """
SELECT sn.nspname AS from_schema,
       sc.relname AS from_table,
       sa.attname AS from_column,
       tn.nspname AS to_schema,
       tc.relname AS to_table,
       ta.attname AS to_column
FROM pg_catalog.pg_constraint con
CROSS JOIN LATERAL unnest(con.conkey, con.confkey) AS k(src, tgt)
JOIN pg_catalog.pg_class sc ON sc.oid = con.conrelid
JOIN pg_catalog.pg_namespace sn ON sn.oid = sc.relnamespace
JOIN pg_catalog.pg_class tc ON tc.oid = con.confrelid
JOIN pg_catalog.pg_namespace tn ON tn.oid = tc.relnamespace
JOIN pg_catalog.pg_attribute sa ON sa.attrelid = con.conrelid AND sa.attnum = k.src
JOIN pg_catalog.pg_attribute ta ON ta.attrelid = con.confrelid AND ta.attnum = k.tgt
WHERE con.contype = 'f'
  AND con.conparentid = 0
  AND sn.nspname = ANY($1::text[])
  AND tn.nspname = ANY($1::text[])
  AND NOT sc.relispartition
  AND NOT tc.relispartition
  AND has_table_privilege(sc.oid, 'SELECT')
  AND has_table_privilege(tc.oid, 'SELECT')
ORDER BY 1, 2, 3, 4, 5, 6
"""


def classify(exc: BaseException) -> DiagnosticCode:
    """Map any connection/introspection failure to one sanitized code."""
    if isinstance(exc, ConnectorError):
        return exc.code
    if isinstance(exc, asyncpg.InvalidAuthorizationSpecificationError):
        return DiagnosticCode.AUTHENTICATION_FAILED
    if isinstance(exc, asyncpg.InvalidCatalogNameError):
        return DiagnosticCode.DATABASE_NOT_FOUND
    if isinstance(exc, asyncpg.InsufficientPrivilegeError):
        return DiagnosticCode.PERMISSION_DENIED
    if isinstance(exc, asyncpg.TooManyConnectionsError):
        return DiagnosticCode.TOO_MANY_CONNECTIONS
    if isinstance(exc, asyncpg.QueryCanceledError | TimeoutError):
        return DiagnosticCode.TIMEOUT
    if isinstance(exc, ssl.SSLError):
        return DiagnosticCode.TLS_ERROR
    if isinstance(exc, ConnectionRefusedError | socket.gaierror):
        return DiagnosticCode.HOST_UNREACHABLE
    if type(exc) is ConnectionError:
        # asyncpg raises a bare ConnectionError when the server rejects the SSL upgrade.
        return DiagnosticCode.TLS_ERROR
    if isinstance(exc, OSError):
        return DiagnosticCode.HOST_UNREACHABLE
    return DiagnosticCode.CONNECTION_FAILED


def _log_failure(operation: str, code: DiagnosticCode, exc: BaseException) -> None:
    # Type and code only: the driver message names the host, port and user (Section 13.1).
    logger.warning(
        "data source %s failed",
        operation,
        extra={"context": {"code": code.value, "error_type": type(exc).__name__}},
    )


class PostgresCatalogConnector:
    engine: Final = "postgres"

    def __init__(
        self,
        *,
        egress: EgressPolicy,
        limits: ConnectorLimits,
        resolver: HostResolver = resolve_host,
    ) -> None:
        self._egress = egress
        self._limits = limits
        self._resolver = resolver

    async def _open(self, target: ConnectionTarget, host: str) -> Any:
        secret = target.secret
        timeout_ms = self._limits.statement_timeout_ms
        return await asyncpg.connect(
            host=host,
            port=secret.port,
            user=secret.username,
            password=secret.password,
            database=target.database_name,
            ssl=secret.sslmode,
            timeout=self._limits.connect_timeout_seconds,
            command_timeout=timeout_ms / 1000 + 1,
            statement_cache_size=0,
            server_settings={
                "application_name": APPLICATION_NAME,
                "default_transaction_read_only": "on",
                "statement_timeout": str(timeout_ms),
                "idle_in_transaction_session_timeout": str(timeout_ms * 2),
            },
        )

    @asynccontextmanager
    async def _connection(self, target: ConnectionTarget) -> AsyncIterator[Any]:
        secret = target.secret
        addresses = await self._resolver(secret.host, secret.port)
        if not self._egress.permits(secret.host, addresses):
            raise ConnectorError(DiagnosticCode.DESTINATION_NOT_ALLOWED)
        hosts = [secret.host] if secret.sslmode == "verify-full" else [str(a) for a in addresses]

        connection: Any = None
        failure: DiagnosticCode = DiagnosticCode.HOST_UNREACHABLE
        for host in hosts:
            try:
                connection = await self._open(target, host)
                break
            except Exception as exc:
                failure = classify(exc)
                if failure is not DiagnosticCode.HOST_UNREACHABLE:
                    _log_failure("connect", failure, exc)
                    raise ConnectorError(failure) from None
        if connection is None:
            raise ConnectorError(failure)
        try:
            yield connection
        finally:
            try:
                await connection.close(timeout=2)
            except Exception:
                connection.terminate()

    async def test(self, target: ConnectionTarget) -> ConnectivityResult:
        started = time.perf_counter()
        try:
            async with self._connection(target) as connection:
                tables = int(
                    await connection.fetchval(COUNT_TABLES_SQL, list(target.allowed_schemas))
                )
        except Exception as exc:
            code = classify(exc)
            if not isinstance(exc, ConnectorError):
                _log_failure("connectivity test", code, exc)
            return ConnectivityResult(
                ok=False,
                code=code,
                message=failure_message(code),
                latency_ms=_elapsed_ms(started),
            )
        return ConnectivityResult(
            ok=True,
            code=DiagnosticCode.CONNECTED,
            message=connected_message(tables),
            latency_ms=_elapsed_ms(started),
            tables_discovered=tables,
        )

    async def introspect(self, target: ConnectionTarget) -> IntrospectedCatalog:
        schemas = list(target.allowed_schemas)
        try:
            async with (
                self._connection(target) as connection,
                connection.transaction(readonly=True, isolation="repeatable_read"),
            ):
                table_count = int(await connection.fetchval(COUNT_TABLES_SQL, schemas))
                if table_count > self._limits.max_tables:
                    raise ConnectorError(DiagnosticCode.CATALOG_TOO_LARGE)
                table_rows = await connection.fetch(TABLES_SQL, schemas)
                column_rows = await connection.fetch(
                    COLUMNS_SQL, schemas, self._limits.max_columns + 1
                )
                if len(column_rows) > self._limits.max_columns:
                    raise ConnectorError(DiagnosticCode.CATALOG_TOO_LARGE)
                fk_rows = await connection.fetch(FOREIGN_KEYS_SQL, schemas)
        except ConnectorError:
            raise
        except Exception as exc:
            code = classify(exc)
            _log_failure("catalog introspection", code, exc)
            raise ConnectorError(code) from None
        return build_catalog(table_rows, column_rows, fk_rows)


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def build_catalog(
    table_rows: Sequence[Any], column_rows: Sequence[Any], fk_rows: Sequence[Any]
) -> IntrospectedCatalog:
    """Rows (mapping-like, with the aliases the catalog queries use) -> the catalog. Shared by
    every connector: the queries differ per engine, the shape does not."""
    columns: dict[tuple[str, str], list[IntrospectedColumn]] = {}
    for row in column_rows:
        columns.setdefault((row["schema_name"], row["table_name"]), []).append(
            IntrospectedColumn(
                name=row["column_name"], data_type=row["data_type"], description=row["description"]
            )
        )
    tables = tuple(
        IntrospectedTable(
            schema_name=row["schema_name"],
            table_name=row["table_name"],
            description=row["description"],
            row_count_estimate=row["row_count_estimate"],
            columns=tuple(columns.get((row["schema_name"], row["table_name"]), ())),
        )
        for row in table_rows
    )
    foreign_keys = tuple(
        dict.fromkeys(
            IntrospectedForeignKey(
                from_schema=row["from_schema"],
                from_table=row["from_table"],
                from_column=row["from_column"],
                to_schema=row["to_schema"],
                to_table=row["to_table"],
                to_column=row["to_column"],
            )
            for row in fk_rows
        )
    )
    return IntrospectedCatalog(tables=tables, foreign_keys=foreign_keys)
