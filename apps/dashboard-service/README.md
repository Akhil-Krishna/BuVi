# dashboard-service

The canonical store for analytics artifacts, and the owner of dashboards and tiles (build spec Sections 3, 8.6, 8.9, 16; Phase A6).

| | |
|---|---|
| Owner | platform / dashboards |
| Schema | `dashboard` (Section 8.6: `artifacts` (immutable for the request role), `dashboards`, `tiles`, `share_links`; RLS) |
| Port | 8007 |
| Public API (via api-gateway) | `GET /api/v1/artifacts/{id}` and `…/data` (`artifact:read`) · `GET /api/v1/dashboards`, `GET /api/v1/dashboards/{id}` (`dashboard:read`) · `POST /api/v1/dashboards`, `POST /api/v1/dashboards/{id}/tiles`, `PATCH /api/v1/tiles/{id}` (`dashboard:pin`) · `POST /api/v1/dashboards/{id}/share-links` (owner, `dashboard:share`, step-up), `GET`/`DELETE …/share-links[/{link_id}]` (owner or `org_admin`) · `GET /api/v1/share/{token}` (public, token-gated; Phase A10) |
| Internal API | `POST /internal/v1/artifacts` (analytics-orchestrator, `dashboard-service:artifacts`) |
| Events | publishes `dashboard.tile.pinned` (JetStream `DASHBOARD`) |
| Contract | `contracts/openapi/dashboard-service.json`, `contracts/events/dashboard.tile.pinned.v1.json` |
| Health | `/health/live`; `/health/ready` (Postgres required; event stream reported) |
| Dependencies | Postgres, identity-service (introspection), visualization-service (ChartSpec validation), query-gateway (stored result rows), NATS |
| Decisions | [ADR 0007](../../docs/adr/0007-phase-a6-visualization-dashboard.md) |
| Runbook | [`docs/runbooks/dashboard-service.md`](../../docs/runbooks/dashboard-service.md) |

## Rules

- Every path id is checked against the caller's tenant; another tenant's id is `404`.
- A `private` dashboard of another user is also `404`. A `tenant` dashboard is readable by the whole tenant, and only its owner may change it.
- Artifact writes are idempotent on the id the run derives. The chart spec is validated by visualization-service before anything is stored.
- Tile `overrides` may only carry Section 17 `options` keys, and the merged spec is re-validated.
- Responses never include `validated_sql` or the result handle.

## Run and test

```bash
make dev-dashboard                      # :8007 (needs make up + make migrate)
uv run --package dashboard-service pytest apps/dashboard-service/src/dashboard_service/tests
make test-dashboards                    # Phase A6 DoD over HTTP against the real stack
```
