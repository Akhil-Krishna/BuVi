# Runbook: analytics-orchestrator

**Owner:** platform / analytics · **Pages on:**
- readiness failing;
- run success rate < 90% over 15 min;
- `analytics.runs` rows stuck in `running` for longer than `ANALYTICS_RUN_TIMEOUT_SECONDS` plus the worker `ack_wait`;
- a burst of `BUDGET_UNAVAILABLE` or `MODEL_UNAVAILABLE`.

## Health

- `GET /health/live`: the process is up.
- `GET /health/ready`: returns `503` when Postgres, Redis (events and token ledger) or the NATS run queue is unavailable.
- identity-service, metadata-service, query-gateway and the model provider are not readiness dependencies. A run that needs one of them while it is down fails with a typed code.

## How a run moves

1. `POST /api/v1/conversations/{id}/messages` (via api-gateway) inserts the message and a `queued` run, then publishes `analytics.run.requested` (JetStream `ANALYTICS`, `Nats-Msg-Id` = run id).
2. worker-runtime calls `POST /internal/v1/runs/{id}/execute`. The executor takes the per-run advisory lock and runs the CrewAI Flow steps.
3. After each step, `runs.flow_state` holds `completed_steps`, `emitted_events` and `usage`. Every event is in `analytics.run_events` before it is published to Redis `analytics:run:{id}`.
4. Log lines `run execution started` (`completed_steps` > 0 means a resume) and `run execution finished` (status) bracket every execution.

## Common incidents

| Symptom | Likely cause | Action |
|---|---|---|
| Runs stay `queued` | worker-runtime down or not consuming; NATS down | Check worker readiness and `nats consumer info ANALYTICS worker-runtime-analytics` (unprocessed / ack pending). Queued runs start when the worker returns. |
| A run was `running` when a pod died | Crash mid-step | Nothing to do: JetStream redelivers after `ack_wait` and the run resumes from its last persisted step. Confirm by the `run execution started` log with `completed_steps` > 0. |
| `RUN_ENQUEUE_FAILED` on runs | NATS unreachable when the message was posted | The run is failed visibly and the user may resend. Restore NATS. |
| Burst of `BUDGET_UNAVAILABLE` | Redis unreachable; the token ledger fails closed by design | Restore Redis. Do not bypass the ledger. |
| `TENANT_BUDGET_EXCEEDED` for one tenant | Daily token cap reached (`llm:tokens:{tenant}:{YYYYMMDD}`) | Expected. Raise `ANALYTICS_TENANT_DAILY_TOKEN_BUDGET` only with a billing decision. Never edit the ledger key by hand. |
| `MODEL_UNAVAILABLE` / `MODEL_REFUSED` | Provider outage, rate limit, or both primary and fallback refused | Check provider status. The router already tried `ANALYTICS_LLM_FALLBACK_MODEL`. |
| `QUERY_REJECTED` rising | The model is producing SQL the validator refuses (after ≤2 repairs), or prompt injection in the catalog | Inspect `query_gateway.query_executions` rows with `purpose='analytics_run'` and `status='rejected'` for the `run_id`. Never widen the allow-list to silence it. |
| `NOT_AUTHORIZED` on queued runs | The user was deactivated or lost `chat:use` after posting | Expected: delegated identity is re-resolved at execution time. |
| SSE clients see no live events | Redis pub/sub down | Clients still get every event through replay (resync every second quiet heartbeat); restore Redis. |
| Startup refuses with "scripted development provider" | `ANALYTICS_LLM_PROVIDER` is not `anthropic` in staging/prod | Set `ANALYTICS_LLM_PROVIDER=anthropic` and provide `ANTHROPIC_API_KEY` from the secret store. |

## Operations

- **Cancel a run:** `POST /api/v1/runs/{id}/cancel`. It takes effect at the next step boundary.
- **Inspect a run** (as `buvi_migrator`):
  ```sql
  SELECT status, error_code, flow_state->'completed_steps', flow_state->'usage'
  FROM analytics.runs WHERE id = '<run>';
  SELECT seq, stage, status, created_at
  FROM analytics.run_events WHERE run_id = '<run>' ORDER BY seq;
  ```
- **Never** delete or update `analytics.run_events` (append-only; `UPDATE`/`DELETE` are revoked). SSE replay depends on it.
- **CrewAI:** telemetry, tracing and the version check are forced off (package `__init__`, Dockerfile). Any egress from this service other than identity, metadata, query-gateway, Redis, NATS, Postgres and the model provider is a defect. See ADR 0006 on the chromadb risk acceptance.

## Deploy / rollback

1. `alembic upgrade head` as `buvi_migrator`, then roll the deployment. In-flight runs resume on the new pods.
2. Rollback: redeploy the previous image. `alembic downgrade -1` drops all conversations and runs; use it only for a failed first deployment.
