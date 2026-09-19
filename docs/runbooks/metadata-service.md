# Runbook: metadata-service

**Owner:** platform / data · **Pages on:** readiness failing; `audit event delivery failed` errors;
sustained `DESTINATION_NOT_ALLOWED` or `AUTHENTICATION_FAILED` spikes from one tenant (Section 22.1)

## Health

- `GET /health/live`: process up. Never checks dependencies.
- `GET /health/ready`: `503` when Postgres or identity-service is unreachable (nothing can be
  authenticated or read). Vault down reports `degraded` but stays `200`: catalog reads still work,
  credential operations answer `503 SECRET_STORE_UNAVAILABLE`.

## Common incidents

| Symptom | Likely cause | Action |
|---|---|---|
| Every request `502 UPSTREAM_UNAVAILABLE` | identity-service down, or this service cannot get a service token | Check identity-service `/health/ready`; check `METADATA_SERVICE_CLIENT_SECRET` matches the `metadata-service` client registered in identity-service. |
| Every request `401 Service authentication required` | Gateway token rejected: rotated signing key, clock skew, or the gateway lacks the `metadata-service:proxy` scope | Check clocks; check identity-service's `api-gateway` client has audience `metadata-service`. |
| `503 SECRET_STORE_UNAVAILABLE` | Vault sealed/unreachable, or token lacks policy on `secret/data/tenants/*` | Check Vault status and the service token's policy. Nothing is written to Postgres when this fires. |
| Test/sync `HOST_UNREACHABLE` for a host that works elsewhere | Network policy, DNS, or wrong port | Resolve and connect from the pod. The diagnostic deliberately omits host/port; get them from Vault, not logs. |
| `DESTINATION_NOT_ALLOWED` for a legitimate internal database | Host resolves to a private address (Section 15) | Only if approved: add the exact hostname to `METADATA_CONNECTOR_ALLOWED_INTERNAL_HOSTS` and record the approval. Never allow-list loopback in staging/prod (startup refuses it). |
| `TLS_ERROR` | `sslmode` stricter than the server supports, or certificate/hostname mismatch with `verify-full` | Fix the server certificate or choose the correct mode. Do not downgrade production connections to `disable`. |
| `CATALOG_TOO_LARGE` | More than `METADATA_CATALOG_MAX_TABLES`/`_MAX_COLUMNS` visible objects | Narrow `allowed_schemas` first; raise limits only knowingly (sync is synchronous until worker-runtime). |
| Log line `audit event delivery failed` (ERROR) | identity-service audit endpoint unavailable after retries | The operation completed but its audit row is missing, and **nothing retries it automatically** (no outbox yet, ADR 0004). Reconstruct the row by hand from the log context (`event_type`, `resource_id`, `tenant_id`, `request_id`; state is not logged) plus the current `metadata.data_sources` row, insert it into `identity.audit_events` as `buvi_migrator`, and note it was replayed; investigate identity-service. |
| Writes fail with "new row violates row-level security" | Code path wrote without a tenant-bound session | Every request path must use `get_repository` (`tenant_scope`). Never grant `BYPASSRLS` to `buvi_app` (Section 19). |

## Security operations

- **Suspected credential exposure for one data source:** rotate the database password at the source,
  then `POST /api/v1/data-sources/{id}/secret` (step-up) and `POST .../test`. The old value in Vault is
  superseded by a new KV version; destroy prior versions with `vault kv destroy` if policy requires.
- **Disable a data source immediately:** `UPDATE metadata.data_sources SET status = 'disabled' WHERE id = '<id>';`
  as `buvi_migrator`. Test, sync and credential changes then answer `409 DATA_SOURCE_DISABLED`.
- **Investigations:** join logs and audit rows on `request_id`. Connector logs carry only `code` and
  `error_type`; a driver's message is never logged, so do not expect hosts or users in logs.

## MySQL data sources (Phase A8)

- `allowed_schemas` lists MySQL **databases**. The catalog shows only objects `information_schema` reveals to the connected user, so a missing table is usually a missing `GRANT SELECT`.
- The read user needs `SELECT` on the listed databases and nothing else; the connector sets a read-only session regardless.
- `UNSUPPORTED_SERVER`: the server is not MySQL 8.0+ (for example MariaDB, TiDB or MySQL 5.7). The server's version is checked on connect, before any catalog query runs. There is no override.
- Dev: `make seed-sample-mysql` (compose service `sample-sales-mysql`, port 3307).

## Per-connection SQL grants (Phase A10, ADR 0013)

- `GET/POST /data-sources/{id}/sql-grants` and `DELETE …/{grant_id}` (`org_admin`). A grant lets one user run SQL-editor queries on one data source; an `org_admin` needs none. Changes are audited as `connection.sql_grant_added` and `connection.sql_grant_revoked`.
- query-gateway asks `GET /internal/v1/data-sources/{id}/sql-grants/{user_id}` on every SQL-editor query and never caches the answer.
- Deleting a data source deletes its grants.

## Deploy / rollback

1. `alembic upgrade head` as `buvi_migrator` (separate job, Section 26), then roll the deployment.
2. Rollback: redeploy the previous image. `alembic downgrade -1` drops every `metadata` table; run it
   only for a failed first deployment with no tenant data.
