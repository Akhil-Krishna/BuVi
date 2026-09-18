# semantic-service

The semantic layer: approved metrics and dimensions that ground the chat flow (build spec Sections 3, 8.3, 12; Phase A7).

| | |
|---|---|
| Owner | platform / semantic layer |
| Schema | `semantic` (Section 8.3: `metrics`, `dimensions`, `join_rules`; RLS; no deletes by the request role) |
| Port | 8008 |
| Public API (via api-gateway, `semantic:manage`) | `GET/POST /api/v1/semantic/metrics`, `GET /api/v1/semantic/metrics/{id}`, `POST …/{id}/approve`, `POST …/{id}/deprecate`, `GET/POST /api/v1/semantic/dimensions` |
| Internal API | `GET /internal/v1/semantic-context?tenant_id=` (analytics-orchestrator, `semantic-service:context`) |
| Contract | `contracts/openapi/semantic-service.json` |
| Health | `/health/live`; `/health/ready` (Postgres) |
| Dependencies | Postgres, identity-service (introspection, audit), metadata-service (catalog lookup) |
| Decisions | [ADR 0010](../../docs/adr/0010-phase-a7-semantic-service.md) |
| Runbook | [`docs/runbooks/semantic-service.md`](../../docs/runbooks/semantic-service.md) |

## Metric expressions (v1)

`SUM | AVG | MIN | MAX | COUNT ( [DISTINCT] column )` over the metric's base table, optionally written `table.column`. It is parsed, checked against the catalog and stored normalized (`AVG(amount)`).

**Refused:** anything else (arithmetic, `CASE`, functions, subqueries, comments), PII columns, tables hidden from agents, and SUM/AVG on non-numeric columns.

## Run and test

```bash
make dev-semantic
uv run --package semantic-service pytest apps/semantic-service/src/semantic_service/tests
make test-semantics        # Phase A7 DoD against the real stack
```
