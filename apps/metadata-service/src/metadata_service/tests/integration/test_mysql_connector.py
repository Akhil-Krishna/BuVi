"""Phase A8: the MySQL catalog connector against a real MySQL 8.4 (the compose sample)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from metadata_service.domain.value_objects.connection import ConnectionSecret, ConnectionTarget
from metadata_service.domain.value_objects.diagnostics import ConnectorError, DiagnosticCode
from metadata_service.infrastructure.connectors.base import ConnectorLimits
from metadata_service.infrastructure.connectors.mysql import MySqlCatalogConnector
from platform_egress import EgressPolicy
from platform_testing.mysql import READER_PASSWORD, READER_USER, MySqlInfo, sample_sales_mysql

pytestmark = [pytest.mark.integration, pytest.mark.security]

LIMITS = ConnectorLimits(
    connect_timeout_seconds=5, statement_timeout_ms=5000, max_tables=100, max_columns=1000
)


@pytest.fixture(scope="module")
def mysql() -> Iterator[MySqlInfo]:
    yield from sample_sales_mysql()


def _target(
    mysql: MySqlInfo, *, password: str = READER_PASSWORD, schemas: tuple[str, ...] = ("sales",)
) -> ConnectionTarget:
    return ConnectionTarget(
        engine="mysql",
        database_name="sales",
        allowed_schemas=schemas,
        secret=ConnectionSecret(
            host=mysql.host,
            port=mysql.port,
            username=READER_USER,
            password=password,
            sslmode="disable",
        ),
    )


def _connector(mysql: MySqlInfo, *, allow: bool = True) -> MySqlCatalogConnector:
    return MySqlCatalogConnector(
        egress=EgressPolicy.from_hosts([mysql.host] if allow else []), limits=LIMITS
    )


async def test_connectivity_counts_tables_and_leaks_nothing(mysql: MySqlInfo) -> None:
    result = await _connector(mysql).test(_target(mysql))
    assert result.ok and result.code is DiagnosticCode.CONNECTED and result.tables_discovered == 5
    assert result.message == "Connected. 5 tables discovered."
    wrong = READER_PASSWORD + "-rotated"
    failed = await _connector(mysql).test(_target(mysql, password=wrong))
    assert (failed.ok, failed.code) == (False, DiagnosticCode.AUTHENTICATION_FAILED)
    assert (
        wrong not in failed.message
        and READER_USER not in failed.message
        and mysql.host not in failed.message
    )
    denied = await _connector(mysql, allow=False).test(_target(mysql))
    assert denied.code is DiagnosticCode.DESTINATION_NOT_ALLOWED


async def test_introspection_reads_tables_columns_comments_and_foreign_keys(
    mysql: MySqlInfo,
) -> None:
    catalog = await _connector(mysql).introspect(_target(mysql))
    tables = {t.table_name: t for t in catalog.tables}
    assert set(tables) == {"customers", "order_items", "orders", "products", "regions"}
    orders = tables["orders"]
    assert orders.schema_name == "sales" and orders.description == "One row per order"
    assert [c.name for c in orders.columns] == [
        "id",
        "customer_id",
        "order_date",
        "status",
        "amount",
    ]
    types = {c.name: c.data_type for c in orders.columns}
    assert types["amount"] == "decimal(12,2)" and types["order_date"] == "date"
    email = next(c for c in tables["customers"].columns if c.name == "email")
    assert email.description == "Contact email (personal data)"
    assert orders.row_count_estimate is not None and orders.row_count_estimate > 0
    fks = {(f.from_table, f.from_column, f.to_table, f.to_column) for f in catalog.foreign_keys}
    assert ("orders", "customer_id", "customers", "id") in fks
    assert ("customers", "region_id", "regions", "id") in fks


async def test_system_databases_are_never_catalogued(mysql: MySqlInfo) -> None:
    """Some performance_schema tables are readable by every user; the connector still lists
    nothing from system databases (the API also refuses these names, see unit tests)."""
    catalog = await _connector(mysql).introspect(
        _target(mysql, schemas=("mysql", "sys", "performance_schema"))
    )
    assert catalog.tables == ()


async def test_catalog_size_cap(mysql: MySqlInfo) -> None:
    small = MySqlCatalogConnector(
        egress=EgressPolicy.from_hosts([mysql.host]),
        limits=ConnectorLimits(
            connect_timeout_seconds=5, statement_timeout_ms=5000, max_tables=2, max_columns=1000
        ),
    )
    with pytest.raises(ConnectorError) as info:
        await small.introspect(_target(mysql))
    assert info.value.code is DiagnosticCode.CATALOG_TOO_LARGE
