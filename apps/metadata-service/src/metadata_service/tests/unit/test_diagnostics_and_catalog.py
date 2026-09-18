"""Driver-failure classification, sanitized messages, catalog checksums, cursors."""

from __future__ import annotations

import socket
import ssl

import asyncpg
import pytest

from metadata_service.application.queries.catalog_queries import decode_cursor, encode_cursor
from metadata_service.domain.errors import InvalidCursorError
from metadata_service.domain.value_objects.catalog import (
    IntrospectedCatalog,
    IntrospectedColumn,
    IntrospectedForeignKey,
    IntrospectedTable,
)
from metadata_service.domain.value_objects.diagnostics import (
    FAILURE_MESSAGES,
    ConnectorError,
    DiagnosticCode,
    connected_message,
    failure_message,
    synced_message,
)
from metadata_service.infrastructure.connectors.mysql import (
    supported_server as mysql_supported_server,
)
from metadata_service.infrastructure.connectors.postgres import classify

pytestmark = pytest.mark.unit

LEAKY = 'password authentication failed for user "reader" at db.internal:5432'


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (asyncpg.InvalidPasswordError(LEAKY), DiagnosticCode.AUTHENTICATION_FAILED),
        (
            asyncpg.InvalidAuthorizationSpecificationError(LEAKY),
            DiagnosticCode.AUTHENTICATION_FAILED,
        ),
        (asyncpg.InvalidCatalogNameError(LEAKY), DiagnosticCode.DATABASE_NOT_FOUND),
        (asyncpg.InsufficientPrivilegeError(LEAKY), DiagnosticCode.PERMISSION_DENIED),
        (asyncpg.TooManyConnectionsError(LEAKY), DiagnosticCode.TOO_MANY_CONNECTIONS),
        (asyncpg.QueryCanceledError(LEAKY), DiagnosticCode.TIMEOUT),
        (TimeoutError(LEAKY), DiagnosticCode.TIMEOUT),
        (ssl.SSLError(LEAKY), DiagnosticCode.TLS_ERROR),
        (
            ConnectionError("PostgreSQL server at db:5432 rejected SSL upgrade"),
            DiagnosticCode.TLS_ERROR,
        ),
        (ConnectionRefusedError(LEAKY), DiagnosticCode.HOST_UNREACHABLE),
        (socket.gaierror(LEAKY), DiagnosticCode.HOST_UNREACHABLE),
        (OSError(LEAKY), DiagnosticCode.HOST_UNREACHABLE),
        (asyncpg.PostgresError(LEAKY), DiagnosticCode.CONNECTION_FAILED),
        (RuntimeError(LEAKY), DiagnosticCode.CONNECTION_FAILED),
        (ConnectorError(DiagnosticCode.CATALOG_TOO_LARGE), DiagnosticCode.CATALOG_TOO_LARGE),
    ],
)
def test_failures_classify_to_one_code_and_a_fixed_message(
    exc: BaseException, code: DiagnosticCode
) -> None:
    assert classify(exc) is code
    message = failure_message(code)
    for fragment in ("reader", "db.internal", "5432", "password authentication"):
        assert fragment not in message


def test_every_failure_code_has_a_message_and_errors_carry_only_the_code() -> None:
    failures = set(DiagnosticCode) - {DiagnosticCode.CONNECTED, DiagnosticCode.SYNCED}
    assert failures == set(FAILURE_MESSAGES)
    assert str(ConnectorError(DiagnosticCode.TIMEOUT)) == "TIMEOUT"


def test_success_messages() -> None:
    assert connected_message(42) == "Connected. 42 tables discovered."
    assert connected_message(1) == "Connected. 1 table discovered."
    assert synced_message(1, 3) == "Catalog synced: 1 table, 3 columns."


def _catalog(*, reverse: bool = False, rows: int | None = 10) -> IntrospectedCatalog:
    tables = [
        IntrospectedTable(
            "sales",
            "orders",
            (IntrospectedColumn("id", "bigint"), IntrospectedColumn("amount", "numeric", "USD")),
            row_count_estimate=rows,
        ),
        IntrospectedTable("sales", "customers", (IntrospectedColumn("id", "integer"),)),
    ]
    fks = [IntrospectedForeignKey("sales", "orders", "customer_id", "sales", "customers", "id")]
    if reverse:
        tables = [
            IntrospectedTable(
                t.schema_name,
                t.table_name,
                tuple(reversed(t.columns)),
                t.description,
                t.row_count_estimate,
            )
            for t in reversed(tables)
        ]
    return IntrospectedCatalog(tuple(tables), tuple(fks))


def test_checksum_ignores_order_and_row_estimates_but_not_structure() -> None:
    base = _catalog().checksum()
    assert _catalog(reverse=True).checksum() == base
    assert _catalog(rows=99_999).checksum() == base
    changed = IntrospectedCatalog(_catalog().tables[:1], ())
    assert changed.checksum() != base
    assert _catalog().column_count == 3


def test_cursor_round_trip_and_rejection() -> None:
    key = ("sales", "order items/ünïcode")
    assert decode_cursor(encode_cursor(key)) == key
    for bad in ("", "!!!", encode_cursor(("only",)), "eyJ4IjoxfQ", "a" * 2000):
        with pytest.raises(InvalidCursorError):
            decode_cursor(bad)


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        ("8.4.3", True),
        ("8.0.36-28", True),
        ("5.7.44", False),
        ("11.4.2-MariaDB-ubu2404", False),
        ("5.5.5-10.11.6-MariaDB", False),
        ("8.0.11-TiDB-v7.5.0", False),
    ],
)
def test_mysql_connector_accepts_only_mysql_8_or_later(version: str, supported: bool) -> None:
    assert mysql_supported_server(version) is supported
    assert failure_message(DiagnosticCode.UNSUPPORTED_SERVER) != failure_message(
        DiagnosticCode.CONNECTION_FAILED
    )
