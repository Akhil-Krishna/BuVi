# Runbook: dashboard-service

**Owner:** platform / dashboards · **Pages on:**
- readiness failing;
- `5xx` rate on `/api/v1/artifacts*` or `/api/v1/dashboards*` above 2% over 15 min;
- a spike in `ARTIFACT_CONFLICT` (should never happen);
- `artifact result handle not readable` in the logs.

## Health

- `GET /health/live`: the process is up.
- `GET /health/ready`: returns `503` when Postgres is unavailable. `checks.events: degraded` means the NATS `DASHBOARD` stream is unreachable; pins still work, but no `dashboard.tile.pinned` events go out.
- visualization-service, query-gateway and identity-service are not readiness dependencies. A request that needs one of them while it is down fails `502`.

## Common incidents

| Symptom | Likely cause | Action |
|---|---|---|
| Runs fail at `artifact.failed` with `UPSTREAM_UNAVAILABLE` | dashboard-service or visualization-service down | Restore the service. Failed runs are terminal; the user resends the message. |
| Runs fail `CHART_INVALID` at the artifact step | visualization-service rejected a spec the Flow had already validated (version skew between deployments) | Check that both services run the same `platform-contracts` version. |
| `GET /artifacts/{id}/data` returns `410 ARTIFACT_RESULT_EXPIRED` | The result handle's TTL (`QUERY_GATEWAY_RESULT_TTL_DAYS`) passed | Expected until artifact refresh exists (ADR 0007 gap). The user re-asks in chat. |
| ERROR log `artifact result handle not readable` | The artifact's `query_result_ref` is not a readable `analytics_run` handle for its tenant | Data integrity: compare `dashboard.artifacts.query_result_ref` with `query_gateway.query_executions.result_handle` for the same run. |
| `ARTIFACT_CONFLICT` | An artifact id reused for a different run (a derived-id collision or a bug) | Treat as a defect. Inspect both runs; never delete the stored artifact. |
| Pins succeed but nothing is notified | NATS down (`events: degraded`), or no consumer yet (Phase A11) | Restore NATS. Events lost during the outage are not replayed. |

## Operations

- **Inspect an artifact** (as `buvi_migrator`):
  ```sql
  SELECT id, run_id, version, created_at FROM dashboard.artifacts WHERE id = '<artifact>';
  ```
- **Artifacts are immutable.** `buvi_app` cannot `UPDATE` or `DELETE` them; do not grant it.
- **Dashboard visibility:** `private` means owner only. `tenant` means readable by the tenant, changeable by the owner only. `link` is reserved for share links (Phase A10).

## Share links and exports (Phase A10, ADR 0013)

- **Create:** `POST /dashboards/{id}/share-links` (the owner, `dashboard:share`, step-up). The token is shown once, stored as SHA-256, and lives at most `DASHBOARD_SHARE_LINK_MAX_HOURS`. A dashboard can have at most `DASHBOARD_MAX_ACTIVE_SHARE_LINKS` active links (`409 SHARE_LINK_LIMIT`).
- **A leaked link:** the owner, or any `org_admin`, calls `DELETE /dashboards/{id}/share-links/{link_id}`. The next guest request gets `404`. Revoking never needs step-up or `dashboard:share`.
- **Guest view:** `GET /share/{token}` is public, rate-limited per IP at the gateway, and sent with `Cache-Control: no-store` and `Referrer-Policy: no-referrer`. It carries names, chart specs and data only. A tile's data is omitted when its result has expired or has more rows than `DASHBOARD_EXPORT_STEP_UP_ROWS`.
- **Exports:** `GET /artifacts/{id}/data` returning more rows than `DASHBOARD_EXPORT_STEP_UP_ROWS` needs a fresh step-up (Section 7.3). Otherwise it is refused with `403 STEP_UP_REQUIRED`.
- **Known gap:** a link created by a user who is later deactivated keeps working until it expires or an admin revokes it. Identity does not notify this service of deactivations.

## Deploy / rollback

1. `alembic upgrade head` as `buvi_migrator`, then roll the deployment.
2. Rollback: redeploy the previous image. `alembic downgrade -1` drops every artifact and dashboard; use it only for a failed first deployment.
