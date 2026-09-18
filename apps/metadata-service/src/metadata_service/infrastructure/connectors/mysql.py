"""The MySQL 8 catalog connector (Phase A8; Sections 13.1, 15, 20).

Same contract and defenses as the Postgres connector, in MySQL terms:

* **Egress (Section 15).** The host is resolved at connect time and every address must pass
  `EgressPolicy`; the driver connects to the checked address (by name only for `verify-full`).
* **Read-only, bounded.** `transaction_read_only = ON` for the session at connect, a server-side
  `max_execution_time`, a connect timeout, `LOCAL INFILE` off, the multi-statement protocol flag
  cleared (aiomysql sets it by default), and caps on
  catalog size.
* **Catalog-only SQL.** Fixed `information_schema` queries with bound parameters. MySQL 8 lists
  only objects the connected user holds a privilege on, so the catalog is what the platform's
  read principal can reach. In MySQL a schema is a database: `allowed_schemas` lists databases.
* **No driver text escapes.** Failures become a `DiagnosticCode`; the driver message is dropped.
"""

from __future__ import annotations

import logging
import ssl
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

import aiomysql
import pymysql
from pymysql.constants import CLIENT

from metadata_service.domain.value_objects.catalog import IntrospectedCatalog
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
from metadata_service.infrastructure.connectors.postgres import build_catalog
from platform_egress import EgressPolicy

logger = logging.getLogger(__name__)

_TABLE_TYPES: Final = "('BASE TABLE', 'VIEW')"
#: Defense in depth behind the API's schema-name check: system databases are never catalogued,
#: even if they reach a query (some performance_schema tables are readable by every user).
_NOT_SYSTEM: Final = "NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')"


def _in(count: int) -> str:
    """Bound-parameter placeholders for an IN list. Only placeholders and constants are ever
    formatted into these queries; every value (schema names, limits) is a bound parameter."""
    return ", ".join(["%s"] * count)


def count_tables_sql(schemas: int) -> str:
    return (
        "SELECT COUNT(*) AS n FROM information_schema.tables "  # noqa: S608 - placeholders only (see _in)
        f"WHERE table_schema IN ({_in(schemas)}) AND table_schema {_NOT_SYSTEM} "
        f"AND table_type IN {_TABLE_TYPES}"
    )


def tables_sql(schemas: int) -> str:
    return (
        "SELECT table_schema AS schema_name, table_name AS table_name, "  # noqa: S608 - placeholders only (see _in)
        "NULLIF(table_comment, '') AS description, "
        "CASE WHEN table_type = 'BASE TABLE' THEN table_rows END AS row_count_estimate "
        "FROM information_schema.tables "
        f"WHERE table_schema IN ({_in(schemas)}) AND table_schema {_NOT_SYSTEM} "
        f"AND table_type IN {_TABLE_TYPES} ORDER BY table_schema, table_name"
    )


def columns_sql(schemas: int) -> str:
    return (
        "SELECT c.table_schema AS schema_name, c.table_name AS table_name, "  # noqa: S608 - placeholders only (see _in)
        "c.column_name AS column_name, c.column_type AS data_type, "
        "NULLIF(c.column_comment, '') AS description "
        "FROM information_schema.columns c "
        "JOIN information_schema.tables t "
        "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
        f"WHERE c.table_schema IN ({_in(schemas)}) AND c.table_schema {_NOT_SYSTEM} "
        f"AND t.table_type IN {_TABLE_TYPES} "
        "ORDER BY c.table_schema, c.table_name, c.ordinal_position LIMIT %s"
    )


def foreign_keys_sql(schemas: int) -> str:
    return (
        "SELECT table_schema AS from_schema, table_name AS from_table, "  # noqa: S608 - placeholders only (see _in)
        "column_name AS from_column, referenced_table_schema AS to_schema, "
        "referenced_table_name AS to_table, referenced_column_name AS to_column "
        "FROM information_schema.key_column_usage "
        f"WHERE referenced_table_name IS NOT NULL AND table_schema IN ({_in(schemas)}) "
        f"AND table_schema {_NOT_SYSTEM} "
        f"AND referenced_table_schema IN ({_in(schemas)}) ORDER BY 1, 2, 3, 4, 5, 6"
    )


class _SingleStatementConnection(aiomysql.Connection):  # type: ignore[misc]
    """aiomysql requests CLIENT.MULTI_STATEMENTS on every connection; clear it before the
    handshake so the server accepts one statement per call (only fixed SQL runs here, but the
    protocol should not allow more)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.client_flag &= ~CLIENT.MULTI_STATEMENTS


async def _connect(**kwargs: Any) -> Any:
    connection = _SingleStatementConnection(**kwargs)
    await connection._connect()
    return connection


def classify(exc: BaseException) -> DiagnosticCode:
    """Map any connection/introspection failure to one sanitized code."""
    if isinstance(exc, ConnectorError):
        return exc.code
    code = exc.args[0] if isinstance(exc, pymysql.MySQLError) and exc.args else None
    if code in (1045, 1698):
        return DiagnosticCode.AUTHENTICATION_FAILED
    if code == 1049:
        return DiagnosticCode.DATABASE_NOT_FOUND
    if code in (1044, 1142, 1143, 1227):
        return DiagnosticCode.PERMISSION_DENIED
    if code == 1040:
        return DiagnosticCode.TOO_MANY_CONNECTIONS
    if code == 3024 or isinstance(exc, TimeoutError):
        return DiagnosticCode.TIMEOUT
    if code in (2002, 2003):
        return DiagnosticCode.HOST_UNREACHABLE
    if isinstance(exc, ssl.SSLError) or code == 2026:
        return DiagnosticCode.TLS_ERROR
    if isinstance(exc, OSError):
        return DiagnosticCode.HOST_UNREACHABLE
    return DiagnosticCode.CONNECTION_FAILED


def _ssl_context(sslmode: str) -> ssl.SSLContext | None:
    if sslmode == "disable":
        return None
    context = ssl.create_default_context()
    if sslmode == "require":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def _log_failure(operation: str, code: DiagnosticCode, exc: BaseException) -> None:
    logger.warning(
        "data source %s failed",
        operation,
        extra={"context": {"code": code.value, "error_type": type(exc).__name__}},
    )


class MySqlCatalogConnector:
    engine: Final = "mysql"

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

    @asynccontextmanager
    async def _connection(self, target: ConnectionTarget) -> AsyncIterator[Any]:
        secret = target.secret
        addresses = await self._resolver(secret.host, secret.port)
        if not self._egress.permits(secret.host, addresses):
            raise ConnectorError(DiagnosticCode.DESTINATION_NOT_ALLOWED)
        hosts = [secret.host] if secret.sslmode == "verify-full" else [str(a) for a in addresses]
        connection: Any = None
        failure = DiagnosticCode.HOST_UNREACHABLE
        for host in hosts:
            try:
                connection = await _connect(
                    host=host,
                    port=secret.port,
                    user=secret.username,
                    password=secret.password,
                    db=target.database_name,
                    ssl=_ssl_context(secret.sslmode),
                    connect_timeout=self._limits.connect_timeout_seconds,
                    charset="utf8mb4",
                    init_command="SET SESSION transaction_read_only = ON",
                    local_infile=False,
                    autocommit=True,
                    program_name="buvi-metadata-service",
                )
                break
            except Exception as exc:
                failure = classify(exc)
                if failure is not DiagnosticCode.HOST_UNREACHABLE:
                    _log_failure("connect", failure, exc)
                    raise ConnectorError(failure) from None
        if connection is None:
            raise ConnectorError(failure)
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    f"SET SESSION max_execution_time = {int(self._limits.statement_timeout_ms)}"
                )
            yield connection
        finally:
            connection.close()

    async def test(self, target: ConnectionTarget) -> ConnectivityResult:
        started = time.perf_counter()
        schemas = list(target.allowed_schemas)
        try:
            async with (
                self._connection(target) as connection,
                connection.cursor(aiomysql.DictCursor) as cursor,
            ):
                await cursor.execute(count_tables_sql(len(schemas)), schemas)
                tables = int((await cursor.fetchone())["n"])
        except Exception as exc:
            code = classify(exc)
            if not isinstance(exc, ConnectorError):
                _log_failure("connectivity test", code, exc)
            return ConnectivityResult(
                ok=False, code=code, message=failure_message(code), latency_ms=_elapsed(started)
            )
        return ConnectivityResult(
            ok=True,
            code=DiagnosticCode.CONNECTED,
            message=connected_message(tables),
            latency_ms=_elapsed(started),
            tables_discovered=tables,
        )

    async def introspect(self, target: ConnectionTarget) -> IntrospectedCatalog:
        schemas = list(target.allowed_schemas)
        try:
            async with (
                self._connection(target) as connection,
                connection.cursor(aiomysql.DictCursor) as cursor,
            ):
                await cursor.execute("START TRANSACTION READ ONLY")
                await cursor.execute(count_tables_sql(len(schemas)), schemas)
                if int((await cursor.fetchone())["n"]) > self._limits.max_tables:
                    raise ConnectorError(DiagnosticCode.CATALOG_TOO_LARGE)
                await cursor.execute(tables_sql(len(schemas)), schemas)
                table_rows = await cursor.fetchall()
                await cursor.execute(
                    columns_sql(len(schemas)), [*schemas, self._limits.max_columns + 1]
                )
                column_rows = await cursor.fetchall()
                if len(column_rows) > self._limits.max_columns:
                    raise ConnectorError(DiagnosticCode.CATALOG_TOO_LARGE)
                await cursor.execute(foreign_keys_sql(len(schemas)), [*schemas, *schemas])
                fk_rows = await cursor.fetchall()
                await connection.rollback()
        except ConnectorError:
            raise
        except Exception as exc:
            code = classify(exc)
            _log_failure("catalog introspection", code, exc)
            raise ConnectorError(code) from None
        return build_catalog(table_rows, column_rows, fk_rows)


def _elapsed(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
