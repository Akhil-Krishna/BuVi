# 0007 — Phase A6: visualization-service and dashboard-service

- **Status:** Accepted · **Date:** 2026-09-17 · **Phase:** A6

## Plan issues found before implementation (fixed in the spec, commit e54692e)

| Issue in the A6 plan | Resolution |
|---|---|
| §9.1's `GET /artifacts/{id}` returns `summary`, but §8.6's `artifacts` table has no such column | `summary TEXT NOT NULL DEFAULT ''` is added to the DDL. |
| §9 says `POST /dashboards` is "create implicit in role", which names no permission | `POST /dashboards` requires `dashboard:pin`. Every role that can pin can create a dashboard; an auditor cannot. |
| No route reads one dashboard with its tiles, but B2's grid view needs one | `GET /dashboards/{id}` is added (`dashboard:read` plus the tenant check). |
| No route serves the rows a chart renders. B2 must "render the returned ChartSpec" without backend changes, but `GET /artifacts/{id}` returns the spec only | `GET /artifacts/{id}/data` is added. dashboard-service reads the stored result through query-gateway's new `POST /internal/v1/results/read`, and answers `410 ARTIFACT_RESULT_EXPIRED` after the handle's TTL. |
| §16 and §8.9 make dashboard-service the canonical artifact store, but Phase A5 kept the artifact in `runs.flow_state` | `persist_artifact` now writes through dashboard-service's internal API. The copy in `flow_state` is removed. |
| §17 puts the validator in visualization-service, but nothing called it; the validator lived in `platform-contracts` | The pure validator moves into visualization-service. The Flow's repair loop, `validate_chart_spec`, and dashboard-service (before storing an artifact or accepting tile overrides) all call it. |
| Tile `overrides` is free-form JSONB, a second injection channel into the renderer | Overrides are limited to §17's `options` keys. The merged spec is re-validated by visualization-service. |
| Nothing defined what `private` and `tenant` visibility mean, or who may change a dashboard | `private` is visible to the owner only (`404` to others). `tenant` is readable tenant-wide. Changes are owner-only (`403 DASHBOARD_NOT_OWNER` when the dashboard is visible). |
| §8.6 includes `share_links`, but share routes need step-up and a tenant sharing policy that do not exist yet | The table is created in A6. `POST /dashboards/{id}/share-links` and `GET /share/{token}` move to Phase A10. |

## Decisions

1. **Validator.** `visualization_service/domain/policies/chart_spec_policy.py` is a pure function over the *raw* JSON object, never a pre-parsed model, so an unknown key is seen and rejected rather than dropped.
   - `POST /internal/v1/chart-specs/validate` answers `200 {valid, chart_spec, problems}`. An invalid spec is a result, not a request error.
   - `problems` name locations and rule types only. Attacker-chosen key names are reported as `<key>`, and input values are never echoed.
   - `platform-contracts` keeps the `ChartSpec` DTO, which is now stricter:
     - no type coercion (`"true"` is not a boolean);
     - exactly one spelling per key (`colorScheme`, not also `color_scheme`);
     - serialized by alias, so a persisted spec round-trips.

2. **Artifact store.**
   - The Flow derives the artifact id as `uuid5(namespace, run_id)`.
   - `POST /internal/v1/artifacts` (scope `dashboard-service:artifacts`, caller `analytics-orchestrator`) is idempotent on that id: `201` when stored, `200` when this run's artifact already exists, `409` for another run.
   - The assistant message is written only if the run has none. A resumed `persist_artifact` therefore creates neither a second artifact nor a second message; before this fix it created both.
   - `buvi_app` has only `SELECT, INSERT` on `dashboard.artifacts`. A change is a new version (§16), never an edit.

3. **Artifact data.**
   - query-gateway parses its own handle format.
   - It serves only a `succeeded` `analytics_run` execution of the named tenant whose stored handle matches exactly, so developer SQL-editor results are never served.
   - Past `created_at + TTL` it answers `RESULT_EXPIRED`, even before the bucket rule sweeps the object. It never re-executes SQL.
   - Only services in `result_readers` (dashboard-service) may call it.

4. **Pinning.**
   - A tile snapshots the artifact's `version` into `chart_spec_version`.
   - A new tile goes on a fresh row: x 0, full default size 6×4, below all other tiles.
   - `dashboard.tile.pinned` is published to JetStream `DASHBOARD` after commit, with `Nats-Msg-Id` set to the tile id. Publishing is best-effort: the pin stands if NATS is down, and readiness reports `events: degraded`.

5. **Service identities.** New dev clients and scopes in identity-service:
   - `dashboard-service`: introspect, `visualization-service:validate`, `query-gateway:results`.
   - `analytics-orchestrator` gains `visualization-service:validate` and `dashboard-service:artifacts`.
   - `api-gateway` gains `dashboard-service:proxy`.

6. **Bugs fixed on the way.**
   - The orchestrator created a new random artifact id and a duplicate assistant message whenever `persist_artifact` re-ran after a crash.
   - `ChartOptions` accepted two spellings of the same key, and silently coerced strings to booleans.

## Gaps — not implemented, need a decision

- **Artifact refresh.** Once the result handle expires (24 h by default), a pinned tile's data returns `410`. Re-executing `validated_sql` needs a phase assignment before C1 (spec, Phase A6).
- **Artifact versioning.** "Make this a stacked bar chart" should create a new version (§16), but §8.6 has no lineage column linking versions. It needs a DDL decision when follow-up instructions are built.
- **`Idempotency-Key` on dashboard and tile creation.** §9 says every mutating endpoint accepts it, but only messages implement it (ADR 0006). A retried pin creates a second tile.
- **`dashboard.tile.pinned` has no consumer** until notification-service (Phase A11), and a failed publish is not retried.
