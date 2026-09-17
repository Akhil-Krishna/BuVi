# Runbook: worker-runtime

**Owner:** platform / analytics · **Pages on:**
- readiness failing for more than 5 min;
- `analytics.run.requested` consumer ack-pending or unprocessed count growing for 15 min;
- messages terminated after `max_deliver`.

## Health

- `GET /health/live`: the process is up.
- `GET /health/ready`: returns `503` until the NATS connection is up and the pull consumer is running. A lost connection is retried with back-off (1 s → 30 s), and readiness reports it in the meantime.

## Message handling

| Orchestrator answer | Disposition |
|---|---|
| `200` (run completed, failed, cancelled, or already finished) | ack |
| `404` (unknown run or tenant) | terminate |
| `409 RUN_BUSY` (another execution holds the run) | nak with delay ≥ 15 s |
| network error / `5xx` | nak with back-off `min(retry_base · 2^(n-1), 120 s)` |
| other `4xx`, malformed body, unknown major `schema_version` | terminate |

While a message is being handled, `in_progress()` heartbeats (every `WORKER_HEARTBEAT_SECONDS`) stop JetStream from redelivering. If the worker dies, JetStream redelivers after `WORKER_ACK_WAIT_SECONDS` (default 360 s, longer than a run's 300 s cap). The orchestrator resumes the run, or returns its final status if the run already finished.

## Common incidents

| Symptom | Likely cause | Action |
|---|---|---|
| Readiness `503`, logs `run consumer failed; reconnecting` | NATS down, or the stream or consumer cannot be created | Restore NATS. The worker reconnects by itself. |
| Log `orchestrator unavailable; will retry` repeating | analytics-orchestrator down or unreachable | Restore the orchestrator. Messages are retried until `max_deliver` (5). |
| Messages terminated after max deliveries | The orchestrator has been down longer than the retry window | The affected runs stay `queued` or `running`. After recovery, re-publish `analytics.run.requested` for those run ids (same `Nats-Msg-Id` = run id). Execution is idempotent. |
| `401`/`403` from the orchestrator | Service client secret or scope misconfigured | Check `WORKER_SERVICE_CLIENT_SECRET` and the identity-service client `worker-runtime` (`analytics-orchestrator:execute`). |

## Scaling

The worker is stateless, so run as many replicas as needed; they share the durable consumer `worker-runtime-analytics`. Per-run single-flight is enforced by the orchestrator's advisory lock, not the worker.

## Deploy / rollback

The worker has no schema. Roll or roll back the image. A message a stopped worker had in flight is redelivered after `ack_wait`.
