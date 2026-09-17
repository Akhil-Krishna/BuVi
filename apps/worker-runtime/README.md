# worker-runtime

Durable queue consumers (build spec Sections 3, 18; Phase A5). Stateless: no database, no public
API. Today it consumes one topic, `analytics.run.requested`, and drives each run's execution in
analytics-orchestrator; a run whose executor or worker dies is redelivered and resumes from its
last persisted step.

| | |
|---|---|
| Owner | platform / analytics |
| Port | 8005 (health only) |
| Consumes | JetStream stream `ANALYTICS`, subject `analytics.run.requested`, durable `worker-runtime-analytics` |
| Calls | analytics-orchestrator `POST /internal/v1/runs/{id}/execute` (scope `analytics-orchestrator:execute`) |
| Health | `/health/live`; `/health/ready` (NATS connected, consumer running) |
| Decisions | [ADR 0006](../../docs/adr/0006-phase-a5-analytics-orchestrator.md) |
| Runbook | [`docs/runbooks/worker-runtime.md`](../../docs/runbooks/worker-runtime.md) |

Message handling: `200` → ack; `404` (unknown run or tenant) → terminate; `409 RUN_BUSY` → retry
later; network error or `5xx` → retry with back-off, up to `max_deliver`; malformed or unknown
major schema version → terminate (Section 18.1).
