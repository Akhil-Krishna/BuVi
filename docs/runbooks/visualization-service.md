# Runbook: visualization-service

**Owner:** platform / visualization · **Pages on:** readiness failing; `5xx` on `/internal/v1/chart-specs/validate`; p95 latency above 200 ms (the validator is pure CPU and should take milliseconds).

## Health

- `GET /health/live`, `GET /health/ready`: stateless, so it is ready as soon as it serves. Verifying service tokens needs identity-service's JWKS; the keys are cached after first use.

## What depends on it

- **analytics-orchestrator:** `build_chart_spec` (repair loop) and `validate_chart_spec`. An outage fails runs with `UPSTREAM_UNAVAILABLE`.
- **dashboard-service:** before storing an artifact and before accepting tile `overrides`. An outage returns `502`, and nothing is stored.

## Common incidents

| Symptom | Likely cause | Action |
|---|---|---|
| `401`/`403` from callers | Token audience or scope misconfigured | Callers need a token for audience `visualization-service` with `visualization-service:validate` (identity-service client registry). |
| Rising `valid: false` for one tenant, with problems such as `<key>: extra_forbidden` | Prompt injection trying to smuggle keys through the chart payload, or a model regression | Expected rejection (§17). Inspect the run's events; never relax the schema to silence it. |
| Valid charts rejected after a deploy | `platform-contracts` version skew between this service and its callers | Deploy all three services with the same `platform-contracts`. |

## Changing the chart vocabulary

§17 makes the vocabulary a closed contract. A new chart type or option is:
1. a spec change;
2. a `platform-contracts` change with a regenerated `contracts/json-schema/ChartSpec.json`;
3. a validator test;
4. a coordinated deploy of visualization-service, analytics-orchestrator and dashboard-service.

## Deploy / rollback

No state: roll or roll back the image.
