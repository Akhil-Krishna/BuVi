# visualization-service

ChartSpec validation and render-safety rules (build spec Sections 3, 17; Phase A6). Stateless: it has no database and makes no outbound calls except fetching identity-service's JWKS.

| | |
|---|---|
| Owner | platform / visualization |
| Port | 8006 |
| API | `POST /internal/v1/chart-specs/validate` only (scope `visualization-service:validate`; callers analytics-orchestrator and dashboard-service) |
| Contract | `contracts/openapi/visualization-service.json`; the vocabulary is `contracts/json-schema/ChartSpec.json` |
| Health | `/health/live`, `/health/ready` |
| Decisions | [ADR 0007](../../docs/adr/0007-phase-a6-visualization-dashboard.md) |
| Runbook | [`docs/runbooks/visualization-service.md`](../../docs/runbooks/visualization-service.md) |

## The validator

`domain/policies/chart_spec_policy.validate_chart_spec(raw_spec, result_schema, overrides=None)` is a pure function over the raw JSON.

**Rejects:**
- any unknown key (never drops it);
- a type or option outside the closed vocabulary;
- markup in text;
- type coercion;
- an encoding that does not fit the artifact's own result schema.

**Overrides:** tile overrides may only carry `options` keys, and the merged spec is validated as a whole.

**Problems:** reported as locations and rule types, never the offending input.

```http
POST /internal/v1/chart-specs/validate
{"chart_spec": {...}, "result_schema": [{"field": "month", "type": "temporal"}], "overrides": {"title": "Q2"}}
-> 200 {"valid": true, "chart_spec": {...normalized...}, "problems": []}
-> 200 {"valid": false, "chart_spec": null, "problems": ["html: extra_forbidden"]}
```

## Run and test

```bash
make dev-visualization
uv run --package visualization-service pytest apps/visualization-service/src/visualization_service/tests
```
