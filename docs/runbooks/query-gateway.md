# Runbook: query-gateway

**Owner:** platform / data security · **Pages on:** readiness failing; execution success rate < 95%
over 15 min (Section 22.1); spike in `QUERY_VALIDATION_FAILED` for one tenant or user (probing signal);
any `secret_ref mismatch` log line

## Health

- `GET /health/live`: process up.
- `GET /health/ready`: `503` when Postgres, identity-service or metadata-service is unreachable.
  Vault or the result store down → `degraded`, still `200`; queries then fail `503` and are audited.

## Common incidents

| Symptom | Likely cause | Action |
|---|---|---|
| Burst of `QUERY_VALIDATION_FAILED` (reasons `WRITE_OPERATION`, `FUNCTION_NOT_ALLOWED`, `TABLE_NOT_ALLOWED`) from one principal | Probing, a prompt-injected agent, or a broken client | Every attempt is in `query_executions` (`status='rejected'`, `validation_result`). Identify `requested_by`/`run_id`; revoke the user's sessions or pause the run. Never add an allow-list entry to silence it. |
| Legitimate query rejected `FUNCTION_NOT_ALLOWED` | Function missing from the allow-list | Review that the function is read-only and cannot block, write, or reach files/network; add it with a corpus test in the same change. |
| `DATA_SOURCE_UNAVAILABLE` | Customer DB down, credentials rotated, network policy, or egress block | Check metadata-service `POST /data-sources/{id}/test`. Pools with failed auth are dropped automatically. |
| ERROR log `secret_ref mismatch` | metadata-service returned a Vault pointer outside the tenant's own path | Treat as a security incident: the pointer was not followed. Inspect `metadata.data_sources.secret_ref` for that id. |
| `QUERY_TIMEOUT` rising | Heavy queries or an overloaded source | Confirm via `duration_ms`; tune per-deployment `QUERY_GATEWAY_DEFAULT_TIMEOUT_MS` knowingly. |
| `429 QUERY_CONCURRENCY_LIMITED` | Tenant hit `QUERY_GATEWAY_TENANT_MAX_CONCURRENT_QUERIES` (per replica) | Expected protection (Section 20). Raise only for that deployment with a record. |
| `503 RESULT_STORE_UNAVAILABLE` | MinIO/S3 down or bucket policy | Restore object storage; the lifecycle rule is re-applied on first write after restart. |

## MySQL data sources (Phase A8, ADR 0011)

- Each MySQL session is read-only, with multi-statements and `LOCAL INFILE` off. `sql_mode` is pinned to MySQL 8's default: a server running `ANSI_QUOTES`/`NO_BACKSLASH_ESCAPES` does not change what executes. `max_execution_time` enforces the query timeout.
- `QUERY_TIMEOUT` on MySQL means `max_execution_time` fired (error 3024).
- `REJECTED_BY_DATABASE` means the read-only transaction or the user's grants refused the statement. Check that the credential is a SELECT-only user.
- Only MySQL 8.0+ is accepted, checked from the server version on every new connection. MariaDB and TiDB are refused before any query runs (log line `data source is not a supported MySQL server`; the caller sees `DATA_SOURCE_UNAVAILABLE`). The fix is the right engine, not a workaround: MariaDB has no `max_execution_time`.

## Public SQL API, grants and concurrency (Phase A10, ADR 0013)

- **`/api/v1/sql/{validate,execute,history}`** is reached only through api-gateway, with the `query-gateway:proxy` scope and the user's own session. It uses purpose `sql_editor`, with the same validator, executor, audit and caps as the chat flow.
- **`403 SQL_GRANT_REQUIRED`:** the caller is not an `org_admin` and has no per-connection grant on that data source. An `org_admin` adds one with `POST /data-sources/{id}/sql-grants` (metadata-service). The check is not cached, so a revocation applies to the next query.
- **`403 STEP_UP_REQUIRED` on execute:** `max_rows` is above `QUERY_GATEWAY_EXPORT_STEP_UP_ROWS` (an export, Section 7.3) and the caller's step-up is not fresh.
- **`429 QUERY_CONCURRENCY_LIMITED`** (with `Retry-After`): the tenant already has `QUERY_GATEWAY_TENANT_MAX_CONCURRENT_QUERIES` queries running, counted across all replicas through Redis leases (`qg:inflight:<tenant>`). A replica that dies frees its slots once the lease expires, after the maximum query timeout plus 60 seconds. If Redis is down, each replica enforces the cap alone (log line `query concurrency store unavailable`), so a tenant can run up to one cap per replica, but never an unlimited number.

## Security operations

- **Audit query:** `SELECT created_at, requested_by, purpose, status, error_code, validation_result->>'reason'
  FROM query_gateway.query_executions WHERE tenant_id = '<id>' ORDER BY created_at DESC LIMIT 100;`
  (`buvi_migrator`; the request role cannot update or delete rows).
- **Result reads for artifacts:** `POST /internal/v1/results/read` (dashboard-service only) serves succeeded `analytics_run` results until `created_at + RESULT_TTL_DAYS`, then `410 RESULT_EXPIRED`. It never re-executes.
- **Result retention:** objects expire via the `query-result-ttl` bucket lifecycle rule (whole days).
  To purge a result early: delete `tenants/<tenant>/queries/<query_id>.json` from the bucket.
- **Read-only principal:** each data source's credential must be a SELECT-only role. The gateway also
  forces read-only transactions, but the database grant is the last line (Section 13).

## Deploy / rollback

1. `alembic upgrade head` as `buvi_migrator`, then roll the deployment.
2. Rollback: redeploy the previous image; `alembic downgrade -1` drops the query audit — only for a
   failed first deployment.
