"""Phase A8 DoD: the unsafe-SQL corpus is 100% rejected in the MySQL dialect too, plus the
MySQL-specific attacks; legitimate MySQL analytics pass and are regenerated in MySQL syntax."""

from __future__ import annotations

import dataclasses

import pytest
import sqlglot
from sqlglot import exp

from query_gateway.domain.policies.sql_validator import RejectionReason as R
from query_gateway.domain.policies.sql_validator import SqlValidator
from query_gateway.domain.value_objects.policy import Purpose
from query_gateway.tests.unit.corpus import AGENT_ONLY_UNSAFE, POLICY, UNSAFE

pytestmark = [pytest.mark.unit, pytest.mark.security]

MYSQL = dataclasses.replace(POLICY, engine="mysql")
VALIDATOR = SqlValidator(max_length=50_000)
ALL_PURPOSES = [Purpose.SQL_EDITOR, Purpose.ANALYTICS_RUN]

MYSQL_UNSAFE: list[tuple[str, str, R]] = [
    # files and server state
    ("into_outfile", "SELECT id FROM sales.orders INTO OUTFILE '/tmp/x'", R.PARSE_ERROR),
    ("into_dumpfile", "SELECT id INTO DUMPFILE '/tmp/x' FROM sales.orders", R.PARSE_ERROR),
    ("into_user_var", "SELECT id INTO @v FROM sales.orders", R.INTO_CLAUSE),
    ("load_file", "SELECT LOAD_FILE('/etc/passwd')", R.FUNCTION_NOT_ALLOWED),
    (
        "load_file_convert",
        "SELECT CONVERT(LOAD_FILE('/etc/passwd') USING utf8mb4)",
        R.FUNCTION_NOT_ALLOWED,
    ),
    (
        "union_load_file",
        "SELECT name FROM sales.regions UNION SELECT LOAD_FILE('/etc/passwd')",
        R.FUNCTION_NOT_ALLOWED,
    ),
    (
        "load_data_local",
        "LOAD DATA LOCAL INFILE '/etc/passwd' INTO TABLE sales.orders",
        R.PARSE_ERROR,
    ),
    ("system_variable", "SELECT @@version", R.PARAMETER),
    ("system_variable_session", "SELECT @@session.sql_mode", R.PARAMETER),
    ("user_variable", "SELECT @x", R.PARAMETER),
    ("user_variable_assign", "SELECT @x := 1", R.PARAMETER),
    ("user_fn", "SELECT USER()", R.FUNCTION_NOT_ALLOWED),
    ("current_user_fn", "SELECT CURRENT_USER()", R.FUNCTION_NOT_ALLOWED),
    ("database_fn", "SELECT DATABASE()", R.FUNCTION_NOT_ALLOWED),
    ("version_fn", "SELECT VERSION()", R.FUNCTION_NOT_ALLOWED),
    ("connection_id", "SELECT CONNECTION_ID()", R.FUNCTION_NOT_ALLOWED),
    ("sys_exec_udf", "SELECT sys_exec('id')", R.FUNCTION_NOT_ALLOWED),
    # blocking and locking
    ("sleep", "SELECT SLEEP(5)", R.FUNCTION_NOT_ALLOWED),
    ("sleep_in_where", "SELECT id FROM sales.orders WHERE SLEEP(1) = 0", R.FUNCTION_NOT_ALLOWED),
    ("benchmark", "SELECT BENCHMARK(1000000, MD5('a'))", R.FUNCTION_NOT_ALLOWED),
    ("get_lock", "SELECT GET_LOCK('a', 10)", R.FUNCTION_NOT_ALLOWED),
    ("for_update", "SELECT id FROM sales.orders FOR UPDATE", R.LOCKING_CLAUSE),
    ("lock_in_share_mode", "SELECT id FROM sales.orders LOCK IN SHARE MODE", R.LOCKING_CLAUSE),
    # statements
    ("handler", "HANDLER sales.orders OPEN", R.PARSE_ERROR),
    ("do_sleep", "DO SLEEP(1)", R.PARSE_ERROR),
    ("call", "CALL sales.purge()", R.WRITE_OPERATION),
    ("set_global", "SET GLOBAL local_infile = 1", R.WRITE_OPERATION),
    ("set_sql_mode", "SET SESSION sql_mode = 'ANSI_QUOTES'", R.WRITE_OPERATION),
    ("replace_into", "REPLACE INTO sales.orders (id) VALUES (1)", R.WRITE_OPERATION),
    (
        "insert_on_duplicate",
        "INSERT INTO sales.orders (id) VALUES (1) ON DUPLICATE KEY UPDATE amount = 0",
        R.WRITE_OPERATION,
    ),
    (
        "delete_join",
        "DELETE o FROM sales.orders o JOIN sales.customers c ON c.id = o.customer_id",
        R.WRITE_OPERATION,
    ),
    (
        "update_join",
        "UPDATE sales.orders o JOIN sales.customers c ON c.id = o.customer_id SET o.amount = 0",
        R.WRITE_OPERATION,
    ),
    ("rename_table", "RENAME TABLE sales.orders TO sales.x", R.WRITE_OPERATION),
    ("create_temporary", "CREATE TEMPORARY TABLE t SELECT 1", R.WRITE_OPERATION),
    ("kill", "KILL 1", R.WRITE_OPERATION),
    ("show_grants", "SHOW GRANTS", R.NOT_A_SELECT),
    ("flush", "FLUSH PRIVILEGES", R.NOT_A_SELECT),
    ("procedure_analyse", "SELECT id FROM sales.orders PROCEDURE ANALYSE()", R.PARSE_ERROR),
    # multiple statements, including MySQL's `#` comment
    ("two_statements", "SELECT 1; DROP TABLE sales.orders", R.MULTIPLE_STATEMENTS),
    ("comment_chain", "SELECT 1 /* x */; DELETE FROM sales.orders", R.MULTIPLE_STATEMENTS),
    ("hash_comment_chain", "SELECT 1 #\n; DROP TABLE sales.orders", R.MULTIPLE_STATEMENTS),
    # catalog probing: MySQL "schemas" are databases
    ("mysql_user", "SELECT * FROM mysql.user", R.TABLE_NOT_ALLOWED),
    ("information_schema", "SELECT table_name FROM information_schema.tables", R.TABLE_NOT_ALLOWED),
    ("performance_schema", "SELECT * FROM performance_schema.threads", R.TABLE_NOT_ALLOWED),
    ("backtick_other_database", "SELECT * FROM `other`.`orders`", R.TABLE_NOT_ALLOWED),
]

MYSQL_SAFE: list[tuple[str, str]] = [
    ("backticks", "SELECT `id`, `amount` FROM `sales`.`orders`"),
    (
        "monthly_bucket",
        "SELECT CAST(DATE_FORMAT(o.order_date, '%Y-%m-01') AS DATE) AS month, SUM(o.amount) AS revenue "
        "FROM sales.orders AS o GROUP BY 1 ORDER BY 1",
    ),
    (
        "date_parts",
        "SELECT YEAR(order_date), MONTH(order_date), QUARTER(order_date) FROM sales.orders",
    ),
    (
        "ifnull_locate",
        "SELECT IFNULL(amount, 0), LOCATE('a', status), CHAR_LENGTH(status) FROM sales.orders",
    ),
    ("limit_offset", "SELECT id FROM sales.orders ORDER BY id LIMIT 5, 10"),
    (
        "date_sub",
        "SELECT id FROM sales.orders WHERE order_date >= DATE_SUB(DATE '2026-06-30', INTERVAL 1 MONTH)",
    ),
    (
        "join_group",
        "SELECT r.name, SUM(o.amount) FROM sales.orders o JOIN sales.customers c ON c.id = o.customer_id "
        "JOIN sales.regions r ON r.id = c.region_id GROUP BY r.name",
    ),
    (
        "cte_window",
        "WITH q AS (SELECT id, amount FROM sales.orders) SELECT id, RANK() OVER (ORDER BY amount DESC) FROM q",
    ),
]


@pytest.mark.parametrize("purpose", ALL_PURPOSES)
@pytest.mark.parametrize(("name", "sql", "reason"), MYSQL_UNSAFE, ids=[c[0] for c in MYSQL_UNSAFE])
def test_mysql_specific_attack_is_rejected(
    name: str, sql: str, reason: R, purpose: Purpose
) -> None:
    outcome = VALIDATOR.validate(sql, MYSQL, purpose=purpose)
    assert (outcome.ok, outcome.reason) == (False, reason), (name, outcome)
    assert outcome.sql is None


def test_the_whole_corpus_is_rejected_in_the_mysql_dialect() -> None:
    """The DoD metric: the shared Section 25 corpus (written for Postgres) plus the MySQL entries,
    parsed as MySQL, is 100% rejected -- whatever reason a different parse leads to."""
    total = rejected = 0
    for purpose in ALL_PURPOSES:
        for _name, sql, _reason in UNSAFE + MYSQL_UNSAFE:
            total += 1
            rejected += not VALIDATOR.validate(sql, MYSQL, purpose=purpose).ok
    for _name, sql, _reason in AGENT_ONLY_UNSAFE:
        total += 1
        rejected += not VALIDATOR.validate(sql, MYSQL, purpose=Purpose.ANALYTICS_RUN).ok
    assert total >= 350
    assert rejected / total == 1.0


@pytest.mark.parametrize(("name", "sql"), MYSQL_SAFE, ids=[c[0] for c in MYSQL_SAFE])
def test_mysql_analytics_pass_and_are_regenerated_as_mysql(name: str, sql: str) -> None:
    outcome = VALIDATOR.validate(sql, MYSQL, purpose=Purpose.ANALYTICS_RUN)
    assert outcome.ok, (name, outcome)
    assert outcome.sql is not None and "`sales`." in outcome.sql and '"' not in outcome.sql
    tree = sqlglot.parse_one(outcome.sql, read="mysql")
    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        assert table.db == "sales" or table.name in ctes


def test_executable_comments_never_reach_the_database() -> None:
    """MySQL runs `/*! ... */` as code; only regenerated SQL executes and it has no comments."""
    outcome = VALIDATOR.validate(
        "SELECT id /*!50000 , LOAD_FILE('/etc/passwd') */ FROM sales.orders",
        MYSQL,
        purpose=Purpose.SQL_EDITOR,
    )
    assert outcome.ok and outcome.sql is not None
    assert "/*" not in outcome.sql and "LOAD_FILE" not in outcome.sql.upper()


def test_backslash_escaped_quote_stays_a_literal() -> None:
    """Regeneration doubles quotes, so the value stays one literal whatever the client wrote."""
    outcome = VALIDATOR.validate(
        r"SELECT id FROM sales.orders WHERE status = 'x\' OR 1=1 -- '",
        MYSQL,
        purpose=Purpose.SQL_EDITOR,
    )
    assert outcome.ok and outcome.sql is not None
    assert outcome.sql.endswith("= 'x'' OR 1=1 -- '")


def test_mysql_pii_policy_applies_on_the_agent_path() -> None:
    outcome = VALIDATOR.validate(
        "SELECT `email` FROM `sales`.`customers`", MYSQL, purpose=Purpose.ANALYTICS_RUN
    )
    assert (outcome.ok, outcome.reason) == (False, R.PII_COLUMN)


@pytest.mark.parametrize("engine", ["snowflake", "bigquery", "redshift", "sqlite"])
def test_an_engine_without_a_dialect_is_never_parsed_as_another(engine: str) -> None:
    outcome = VALIDATOR.validate(
        "SELECT id FROM sales.orders",
        dataclasses.replace(POLICY, engine=engine),
        purpose=Purpose.SQL_EDITOR,
    )
    assert (outcome.ok, outcome.reason) == (False, R.PARSE_ERROR)
