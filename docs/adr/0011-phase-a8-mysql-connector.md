# 0011 — Phase A8: MySQL connector, per-dialect validation

- **Status:** Accepted · **Date:** 2026-09-18 · **Phase:** A8 (replanned in spec commit 5ee09af)

## Plan issues fixed before implementation

| Issue | Resolution |
|---|---|
| "MySQL, then Snowflake/BigQuery": the warehouses have no local or CI instance, so their adapters would be code nothing can run | A8 is **MySQL end to end**. Snowflake, BigQuery and Redshift are post-GA backlog, preconditioned on vendor sandbox accounts in CI. Their `engine` values stay and are refused `ENGINE_NOT_SUPPORTED`. |
| "Dialect-specific SQL" had no plan; the validator, the SQL prompt, the scripted generator and the A7 metric check all assumed Postgres | The validator parses and regenerates in the data source's dialect. The Flow carries the engine in the agent context, tells the generator the dialect, and parses the metric check in it. |
| The DoD tested only the parser | It now also requires the database-side defenses: a read-only session, no multi-statements, no `LOCAL INFILE`, a SELECT-only user, server-side timeout, and caps. |
| MySQL has no schemas in the Postgres sense | `allowed_schemas` lists **databases** for MySQL. A MySQL twin of the sample (same rows) runs in compose. |

## Decisions

1. **One validator, per-dialect parsing** (`DIALECTS = {"postgres", "mysql"}`).
   - An engine without a dialect is rejected, never parsed as another.
   - The allow-list is shared, plus a per-dialect list of builtins sqlglot does not model. MySQL's list omits `SLEEP`, `BENCHMARK`, `LOAD_FILE`, `GET_LOCK`, `USER()`, `CONNECTION_ID()` and UDFs.
   - `@user_var` / `@@system_var` (`SessionParameter`) are now rejected. **Bug fixed:** `SELECT @@version` was not blocked before, and would have disclosed server variables on MySQL.
   - Date-part builtins and sqlglot's implicit conversion nodes are allowed.
   - Only regenerated SQL executes, and it carries no comments, so MySQL's executable `/*! … */` comments never reach the database. A test covers it.
2. **MySQL executor** (`query_gateway/infrastructure/connectors/mysql.py`, aiomysql):
   - **Isolation:** multi-statements off, `LOCAL INFILE` off, `transaction_read_only = ON` for the session at connect, and each query inside `START TRANSACTION READ ONLY`.
   - **Limits:** `max_execution_time` per query, streaming with an unbuffered cursor under row and byte caps, and a truncated connection is discarded rather than drained.
   - **Egress:** checked at pool creation, as for Postgres.
   - **Pinned `sql_mode`:** MySQL 8's default, without `ANSI_QUOTES` or `NO_BACKSLASH_ESCAPES`. That is the syntax sqlglot parses and regenerates; a server configured otherwise would otherwise reinterpret quotes and backslashes, a parser differential.
3. **MySQL catalog connector** (metadata-service): fixed `information_schema` queries with bound parameters. MySQL 8 lists only objects the connected user holds a privilege on. The same `build_catalog` shape serves both engines.
4. **Shared test fixture.** `platform-testing` (the §4 shared-fixtures package, unused until now) provides the MySQL 8.4 container loaded with the compose init SQL, for both services. It is installed through the root `dev` group, so it never ships in a service image.

## Found by the real-database tests (all fixed before commit)

- **aiomysql turns multi-statements on for every connection.** It hard-codes `client_flag |= CLIENT.MULTI_STATEMENTS`. A capability-constant check had wrongly suggested the flag was off. Against real MySQL, `SELECT 1; DROP TABLE …` reached the server and only the read-only session stopped it.
  - **Fix:** connections are built with the flag cleared before the handshake, in both services. The executor pools them itself, because aiomysql's pool builds its own.
  - **Test:** the server now rejects the second statement as a syntax error, and the table is untouched.
- **aiomysql sends `sql_mode=` unquoted**, so a mode list was a syntax error and every connection failed. The pinned mode and the read-only session are now one `init_command` statement we write.
- **MySQL system databases are readable.** Some `performance_schema` tables are readable by any user, so listing a system database would have catalogued server state for agents. `mysql`, `performance_schema` and `sys` are now refused as schema names at the API, like `pg_*`/`information_schema`, and excluded inside the catalog queries as defense in depth.
- **The seed SQL used `lines`, a MySQL reserved word**, as a CTE name. It was renamed.

## Verification

- **Validator:** the whole shared corpus, plus 45 MySQL-specific attacks, is rejected in the MySQL dialect (`test_sql_validator_mysql.py`). The Postgres corpus is unchanged.
- **Database side** (`test_mysql_executor.py`, real MySQL):
  - writes with **root** credentials are refused by the read-only session;
  - the reader is SELECT-only;
  - multi-statements and `LOAD DATA LOCAL` fail;
  - `max_execution_time` stops a runaway join;
  - the pinned `sql_mode` holds on a server switched to `ANSI_QUOTES,NO_BACKSLASH_ESCAPES`;
  - caps are flagged; egress is denied; no credential appears in errors.
- **Catalog** (`test_mysql_connector.py`): table and column types, comments, foreign keys, privilege-scoped visibility, and the size cap.
- **Live:** `make test-mysql-slice` runs Section 32 against MySQL with an approved metric. The answer must equal MySQL's own and the Postgres twin's (identical data: 2,000 orders, 4,001 lines, total 705,332.50).

## Gaps

- **Warehouses** (Snowflake, BigQuery, Redshift): post-GA backlog, as above.
- **MariaDB is not supported as `mysql`.** It lacks `max_execution_time` (it uses `max_statement_time`) and differs in the data dictionary.
- **TLS `verify-full` for MySQL is untested live.** The compose MySQL has no certificate; the code path is the same as Postgres's (connect by name, verify).
