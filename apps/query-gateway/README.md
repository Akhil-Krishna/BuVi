# query-gateway

The hard security boundary for SQL against customer databases (build spec Sections 3, 8.5, 13;
Phase A4). The only service that executes arbitrary or business SQL with customer credentials.

| | |
|---|---|
| Owner | platform / data security |
| Schema | `query_gateway` (Section 8.5; `query_executions`, append-only, RLS) |
| Port | 8003 |
| API | `POST /internal/v1/queries` only — no public route in Phase A4; contract `contracts/openapi/query-gateway.json` |
| Health | `/health/live`; `/health/ready` (Postgres, identity-service, metadata-service required; Vault and result store reported) |
| Dependencies | Postgres (`buvi_app`), identity-service (introspection, service tokens), metadata-service (query policy), Vault KV v2, MinIO/S3 (result handles), customer databases (egress-controlled) |
| Decisions | [ADR 0005](../../docs/adr/0005-phase-a4-query-gateway.md) |
| Runbook | [`docs/runbooks/query-gateway.md`](../../docs/runbooks/query-gateway.md) |

## Request

```http
POST /internal/v1/queries
X-Service-Authorization: Bearer <service JWT, scope query-gateway:execute>
Cookie: buvi_session=<the end user's session>   (or Authorization: Bearer <API key>)

{"database_id": "…", "sql": "SELECT …", "purpose": "sql_editor", "max_rows": 1000, "timeout_ms": 30000}
```

| Purpose | Calling service | User permission | Column policy |
|---|---|---|---|
| `sql_editor` | `api-gateway` | `sql:execute` | catalog tables and columns |
| `analytics_run` | `analytics-orchestrator` | `chat:use` | also no PII columns, no agent-hidden tables |
| `export` | — | — | `422 PURPOSE_NOT_SUPPORTED` |

## Pipeline (Section 13)

service token → user introspection → purpose/permission → policy from metadata-service (tenant-bound)
→ `SqlValidator` (single SELECT, no writes anywhere, function allow-list, catalog-bound identifiers,
agent PII policy; executes the regenerated SQL, never the caller's text) → per-tenant concurrency cap
→ credential from Vault (pointer pinned to the tenant's own path) → read-only execution (one prepared
statement, read-only transaction, empty `search_path`, statement timeout, row and byte caps, egress
policy) → result handle in object storage with TTL → `query_executions` row for every outcome.

Tests: `uv run --package query-gateway pytest apps/query-gateway/src/query_gateway/tests` — the
unsafe-SQL corpus is `tests/unit/corpus.py`. Live flow: `make test-query-gateway`.
