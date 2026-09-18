"""Phase A8: MySQL execution defenses, proven against a real MySQL 8.4 -- independently of the
validator (Section 13: "never rely on the parser alone")."""

from __future__ import annotations

from collections.abc import Iterator

import pymysql
import pytest

from platform_egress import EgressPolicy
from platform_testing.mysql import (
    READER_PASSWORD,
    ROOT_PASSWORD,
    MySqlInfo,
    mariadb,
    sample_sales_mysql,
)
from query_gateway.domain.value_objects.execution import (
    ConnectionCredentials,
    ExecutionError,
    ExecutionFailure,
    ExecutionLimits,
)
from query_gateway.infrastructure.connectors.mysql import MySqlQueryExecutor

pytestmark = [pytest.mark.integration, pytest.mark.security]

LIMITS = ExecutionLimits(max_rows=1000, max_bytes=1_000_000, timeout_ms=10_000)


@pytest.fixture(scope="module")
def mysql() -> Iterator[MySqlInfo]:
    yield from sample_sales_mysql()


def _credentials(
    mysql: MySqlInfo, user: str = "buvi_reader", password: str = READER_PASSWORD
) -> ConnectionCredentials:
    return ConnectionCredentials(
        host=mysql.host, port=mysql.port, username=user, password=password, sslmode="disable"
    )


@pytest.fixture
async def executor(mysql: MySqlInfo) -> MySqlQueryExecutor:
    return MySqlQueryExecutor(
        egress=EgressPolicy.from_hosts([mysql.host]),
        connect_timeout_seconds=5,
        pool_max_size=2,
        pool_idle_seconds=60,
    )


async def _run(
    executor: MySqlQueryExecutor,
    mysql: MySqlInfo,
    sql: str,
    *,
    limits: ExecutionLimits = LIMITS,
    **creds: str,
):
    try:
        return await executor.execute(
            pool_key="ds-1",
            database_name="sales",
            credentials=_credentials(mysql, **creds),
            sql=sql,
            limits=limits,
        )
    finally:
        await executor.close()


async def test_validated_select_runs_with_typed_columns(
    executor: MySqlQueryExecutor, mysql: MySqlInfo
) -> None:
    result = await _run(
        executor,
        mysql,
        "SELECT CAST(DATE_FORMAT(`o`.`order_date`, '%Y-%m-01') AS DATE) AS `month`, "
        "SUM(`o`.`amount`) AS `revenue`, COUNT(*) AS `orders` FROM `sales`.`orders` AS `o` "
        "WHERE `o`.`order_date` >= DATE '2026-04-01' AND `o`.`order_date` < DATE '2026-07-01' "
        "GROUP BY 1 ORDER BY 1",
    )
    assert [(c.name, c.type) for c in result.columns] == [
        ("month", "date"),
        ("revenue", "decimal"),
        ("orders", "bigint"),
    ]
    assert [r[0] for r in result.rows] == ["2026-04-01", "2026-05-01", "2026-06-01"]
    assert isinstance(result.rows[0][1], str) and not result.truncated


async def test_row_and_byte_caps_are_flagged(
    executor: MySqlQueryExecutor, mysql: MySqlInfo
) -> None:
    rows = await _run(
        executor,
        mysql,
        "SELECT `id` FROM `sales`.`orders` ORDER BY `id`",
        limits=ExecutionLimits(max_rows=10, max_bytes=1_000_000, timeout_ms=10_000),
    )
    assert rows.row_count == 10 and (rows.truncated, rows.truncation_reason) == (True, "rows")
    small = await _run(
        executor,
        mysql,
        "SELECT `id`, `name` FROM `sales`.`customers`",
        limits=ExecutionLimits(max_rows=1000, max_bytes=200, timeout_ms=10_000),
    )
    assert (small.truncated, small.truncation_reason) == (
        True,
        "bytes",
    ) and small.bytes_returned <= 200


async def test_writes_are_refused_by_the_database_even_with_root_credentials(
    executor: MySqlQueryExecutor, mysql: MySqlInfo
) -> None:
    """The read-only session and transaction hold even if the credential could write."""
    for sql in (
        "INSERT INTO `sales`.`regions` (`id`, `name`) VALUES (99, 'x')",
        "UPDATE `sales`.`orders` SET `amount` = 0",
        "DELETE FROM `sales`.`orders`",
    ):
        with pytest.raises(ExecutionError) as info:
            await _run(executor, mysql, sql, user="root", password=ROOT_PASSWORD)
        assert info.value.failure is ExecutionFailure.REJECTED_BY_DATABASE, sql
    with (
        pymysql.connect(
            host=mysql.host, port=mysql.port, user="root", password=ROOT_PASSWORD, database="sales"
        ) as db,
        db.cursor() as cur,
    ):
        cur.execute("SELECT COUNT(*) FROM regions WHERE id = 99")
        assert cur.fetchone() == (0,)


async def test_reader_has_select_only(executor: MySqlQueryExecutor, mysql: MySqlInfo) -> None:
    with pytest.raises(ExecutionError) as info:
        await _run(executor, mysql, "INSERT INTO `sales`.`regions` (`id`, `name`) VALUES (98, 'x')")
    assert info.value.failure is ExecutionFailure.REJECTED_BY_DATABASE


async def test_multi_statements_and_local_infile_are_off(
    executor: MySqlQueryExecutor, mysql: MySqlInfo
) -> None:
    # aiomysql requests CLIENT.MULTI_STATEMENTS by default; the executor clears it, so the server
    # itself rejects the second statement (a syntax error), not merely the read-only session.
    for sql in ("SELECT 1; SELECT 2", "SELECT 1; DROP TABLE sales.orders"):
        with pytest.raises(ExecutionError) as info:
            await _run(executor, mysql, sql, user="root", password=ROOT_PASSWORD)
        assert info.value.failure is ExecutionFailure.QUERY_FAILED, sql
    with mysql.root("sales") as db, db.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM orders")
        assert cur.fetchone() == (2000,)
    with pytest.raises(ExecutionError):
        await _run(
            executor,
            mysql,
            "LOAD DATA LOCAL INFILE '/etc/hosts' INTO TABLE sales.regions",
            user="root",
            password=ROOT_PASSWORD,
        )


async def test_server_side_timeout_stops_a_runaway_query(
    executor: MySqlQueryExecutor, mysql: MySqlInfo
) -> None:
    with pytest.raises(ExecutionError) as info:
        await _run(
            executor,
            mysql,
            # 8 billion rows that must really be read (COUNT(*) alone is answered from stats).
            "SELECT SUM(`a`.`amount` * `b`.`amount` * `c`.`amount`) "
            "FROM `sales`.`orders` AS `a`, `sales`.`orders` AS `b`, `sales`.`orders` AS `c`",
            limits=ExecutionLimits(max_rows=10, max_bytes=10_000, timeout_ms=500),
        )
    assert info.value.failure is ExecutionFailure.TIMEOUT


async def test_session_sql_mode_is_pinned_against_server_configuration(
    executor: MySqlQueryExecutor, mysql: MySqlInfo
) -> None:
    """A server running ANSI_QUOTES would read "x" as an identifier; the pinned session mode keeps
    the syntax the validator parsed (double quotes are strings, backslash escapes on)."""
    with (
        pymysql.connect(
            host=mysql.host, port=mysql.port, user="root", password=ROOT_PASSWORD
        ) as db,
        db.cursor() as cur,
    ):
        cur.execute("SET GLOBAL sql_mode = 'ANSI_QUOTES,NO_BACKSLASH_ESCAPES'")
    try:
        result = await _run(executor, mysql, "SELECT \"x\" AS `v`, 'a\\'b' AS `w`")
        assert result.rows == [["x", "a'b"]]
    finally:
        with (
            pymysql.connect(
                host=mysql.host, port=mysql.port, user="root", password=ROOT_PASSWORD
            ) as db,
            db.cursor() as cur,
        ):
            cur.execute("SET GLOBAL sql_mode = DEFAULT")


async def test_failures_are_sanitized_and_egress_is_enforced(
    executor: MySqlQueryExecutor, mysql: MySqlInfo
) -> None:
    with pytest.raises(ExecutionError) as info:
        await _run(executor, mysql, "SELECT 1", password="wrong-password-xyz")
    assert info.value.failure is ExecutionFailure.AUTHENTICATION_FAILED
    assert "wrong-password-xyz" not in repr(info.value) and "buvi_reader" not in str(info.value)
    closed = MySqlQueryExecutor(
        egress=EgressPolicy(), connect_timeout_seconds=5, pool_max_size=1, pool_idle_seconds=60
    )
    with pytest.raises(ExecutionError) as denied:
        await _run(closed, mysql, "SELECT 1")
    assert denied.value.failure is ExecutionFailure.DESTINATION_NOT_ALLOWED


@pytest.fixture(scope="module")
def maria() -> Iterator[MySqlInfo]:
    yield from mariadb()


async def test_mariadb_is_refused_by_server_identity(maria: MySqlInfo) -> None:
    """MariaDB has no max_execution_time: running there would lose the server-side timeout,
    so the executor refuses the server instead of running with part of its defenses."""
    executor = MySqlQueryExecutor(
        egress=EgressPolicy.from_hosts([maria.host]),
        connect_timeout_seconds=5,
        pool_max_size=1,
        pool_idle_seconds=60,
    )
    with pytest.raises(ExecutionError) as info:
        await _run(executor, maria, "SELECT 1", user="root", password=ROOT_PASSWORD)
    assert info.value.failure is ExecutionFailure.UNAVAILABLE
