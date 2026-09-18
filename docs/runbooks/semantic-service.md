# Runbook: semantic-service

**Owner:** platform / semantic layer · **Pages on:**
- readiness failing;
- runs failing `UPSTREAM_UNAVAILABLE` at `semantic.failed`;
- a spike in `SEMANTIC_DEFINITION_INVALID` on approval (catalog drift).

## Health

- `GET /health/live`: the process is up.
- `GET /health/ready`: returns `503` when Postgres is unavailable.
- metadata-service is needed only to create, approve or define. Its outage gives those routes `502`; reads and the Flow's context keep working.

## How it is used

1. A developer or org_admin defines a metric (`POST /api/v1/semantic/metrics`) with a v1 expression (`AGG([DISTINCT] column)`) on a catalog table. It is stored as `draft`, and drafts are never used.
2. `POST …/approve` re-checks the catalog and makes it available to the Flow. `…/deprecate` withdraws it. All three steps are audited (`identity.audit_events`, `semantic.metric.*`).
3. analytics-orchestrator reads approved metrics on every run (`/internal/v1/semantic-context`). A resolved metric fixes the query's aggregation; the run records it in `flow_state.grounding`.

## Common incidents

| Symptom | Likely cause | Action |
|---|---|---|
| Every run fails at `semantic.failed` | semantic-service down or unreachable | Restore it. Runs fail closed by design, rather than guessing a defined metric. |
| Approval refused, `expression: column is PII` or `table is hidden from agents` | The catalog changed since the draft was written | Expected. Fix the definition or the column classification; never bypass. |
| "The chart ignores our Revenue definition" | Metric not `approved`, deprecated, or its base table is not in the data source the run used | `GET /api/v1/semantic/metrics?status=approved`; check the run's `flow_state->'grounding'`. |
| `approved metric has an invalid expression` in the logs | A stored expression was edited outside the API | Treat as a defect. Deprecate it and redefine through the API. |

## Operations

- **Which runs used a metric:** `SELECT id FROM analytics.runs WHERE flow_state->'grounding'->'metric_ids' ? '<metric id>';`
- **Groundedness report:** `make eval-groundedness`.

## Deploy / rollback

1. `alembic upgrade head` as `buvi_migrator`, then roll the deployment.
2. Rollback: redeploy the previous image. `alembic downgrade -1` drops every definition; use it only for a failed first deployment.
