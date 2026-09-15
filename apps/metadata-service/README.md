# metadata-service

Data-source connection metadata (never secret plaintext at rest), the schema catalog,
and catalog sync (build spec Sections 3, 8.2, 12, 13.1; Phase A3).

| | |
|---|---|
| Owner | platform / data |
| Schema | `metadata` (Section 8.2; migration `migrations/versions/0001_metadata_schema.py`) |
| Port | 8002 |
| Health | `GET /health/live`; `GET /health/ready` (Postgres + identity-service required, Vault reported) |
| API | `/api/v1`, reached only through api-gateway; contract `contracts/openapi/metadata-service.json` |
| Auth | forwarded session/API key re-authenticated via identity-service introspection; api-gateway service token (`metadata-service:proxy`) |
| Dependencies | Postgres (`buvi_app`, RLS-bound), identity-service (introspection, audit), Vault KV v2, customer databases (egress-controlled) |
| Decisions | [ADR 0004](../../docs/adr/0004-phase-a3-metadata-service.md) |
| Runbook | [`docs/runbooks/metadata-service.md`](../../docs/runbooks/metadata-service.md) |

## Endpoints

All require `data:manage` (Section 7.1: `developer`, `org_admin`). Every `{data_source_id}`
route also checks the resource belongs to the caller's tenant: cross-tenant ids return `404`.

| Method & path | Notes |
|---|---|
| `GET /data-sources` | tenant-scoped, paginated |
| `POST /data-sources` | non-secret metadata only; `pending` until credentials are set |
| `GET /data-sources/{id}` | status, `last_sync_at` (beyond Section 9, ADR 0004) |
| `POST /data-sources/{id}/secret` | **step-up**; `{host, port, username, password, sslmode}` written to Vault, never returned |
| `POST /data-sources/{id}/test` | bounded connectivity check; sanitized code + message only |
| `POST /data-sources/{id}/sync` | introspect → `tables`/`columns`/`relationships` (synchronous until worker-runtime) |
| `GET /data-sources/{id}/tables` | catalog, paginated (beyond Section 9, ADR 0004) |
| `GET /data-sources/{id}/tables/{table_id}` | columns + relationships (beyond Section 9, ADR 0004) |

## Security properties

- Credentials exist only in Vault (`secret/data/tenants/<tenant>/datasources/<id>`) and in memory
  while a test or sync runs. No table, response, log line or audit row carries them; driver errors
  are reduced to a fixed diagnostic code (Section 13.1).
- Outbound connections obey Section 15: resolved addresses must be public unless the host is
  explicitly allow-listed, and the connector connects to the addresses it validated.
- Introspection is read-only (`default_transaction_read_only`, statement timeout, size caps) and only
  catalogs objects the connected role can `SELECT`, within `allowed_schemas`.
- RLS on every table, bound per transaction; the service runs as `buvi_app`.

## Local development

```bash
make up && make migrate && make seed   # infra, schemas, Keycloak, demo tenant, sample-sales-db
make dev & make dev-metadata & make dev-gateway
make test-data-sources                 # Phase A3 scripted end-to-end flow
```

Tests: `uv run --package metadata-service pytest apps/metadata-service/src/metadata_service/tests`
(integration tests start one Postgres container that serves as both the platform database and a
customer database).
