# 0006 — Phase A5: analytics-orchestrator, CrewAI Flow, worker-runtime

- **Status:** Accepted · **Date:** 2026-09-17 · **Phase:** A5

## Plan issues found before implementation (fixed in the spec, commit e39fcc7)

| Issue in the A5 plan | Resolution |
|---|---|
| §11's stage enum had no stage for the artifact write or for the run's end, and §32 listed only some events | Stages now include `artifact` and `run`. §32 lists the full `<stage>.started/completed` sequence. A failure is `<stage>.failed` followed by exactly one `run.failed`. |
| Where the Flow executes was contradictory: worker-runtime "runs the Flow", but the Flow's state, events and DB live in analytics-orchestrator | The Flow executes in analytics-orchestrator. worker-runtime is the durable JetStream driver that calls `POST /internal/v1/runs/{id}/execute`. |
| A queued run has no user session to forward to query-gateway (§13 required one) | Delegated `on_behalf_of` is added. It is allowed only for caller `analytics-orchestrator` with purpose `analytics_run` and a `run_id`, and query-gateway re-resolves the principal through identity-service. |
| `validate_sql` needs the validator, but only `POST /internal/v1/queries` existed, and a second validator would drift | query-gateway adds `POST /internal/v1/queries/validate`, which uses the same validator and writes a `validated` or `rejected` audit row. |
| Agents need schema context, but no route served it without `secret_ref` or PII | metadata-service adds `GET /internal/v1/data-sources` and `…/{id}/context`. They return visible tables and non-PII columns only. |
| ChartSpec (§17) and artifacts belong to A6 services that do not exist yet | ChartSpec is a strict model in `platform-contracts`. The artifact record stays in `runs.flow_state` until A6 moves it. |
| §9.1's message body had no data-source selection and no retry safety | Optional `data_source_id` and an `Idempotency-Key` header are added. |
| Budgets were named but had no numbers | Defaults: run 60k tokens, tenant 2M tokens/day, 90 s per stage, 300 s per run, ≤2 repairs. All are configurable (`ANALYTICS_*`). |

## Decisions

1. **The Flow is a CrewAI `Flow` (`infrastructure/flow/analytics_flow.py`).** It has 12 `@start`/`@listen` steps in §10 order. Each step calls `RunExecutor.run_step`. The executor:
   - skips steps already in `completed_steps`;
   - checks for cancellation;
   - emits `<stage>.started`;
   - runs the step under the stage timeout;
   - persists the step, then emits `<stage>.completed`.

   CrewAI's own `FlowPersistence` is synchronous and keeps a separate store, so it is not used. `runs.flow_state` is the only persisted state. CrewAI builds its own state instance from `kickoff_async(inputs=…)`, so failure handling reads progress and emitted events from the persisted row, and token usage from the live instance.

2. **No CrewAI `Agent`/`Crew` objects.** Each LLM stage (intent, plan, SQL, chart) makes one typed call through `ModelRouter` with a pydantic output model. CrewAI's agent loop would add hidden tool use, free-text hops and untracked tokens, which conflicts with §10.3 (typed hops, bounded repair) and §23 (every token budgeted). The stages keep the §10 names so agents can be introduced later behind the same port.

3. **Execution control.**
   - A Postgres advisory lock per run gives single-flight execution. A second caller gets `409 RUN_BUSY`.
   - Every event is appended to `analytics.run_events` (the record, append-only) and recorded in `emitted_events` before it is published to Redis `analytics:run:{id}` (transport only).
   - After a crash, the redelivered message resumes the run. Events are never duplicated.
   - worker-runtime acks on `200`, retries `409` and `5xx` with back-off, terminates on `404` or malformed messages, and sends `in_progress` heartbeats while a run executes.

4. **Model routing (§23).** `ModelRouter` works as follows:
   - **Before a call:** it checks the run cap using an estimate (`len/3 + max_tokens`), then the tenant's daily ledger (Redis `llm:tokens:{tenant}:{YYYYMMDD}`). An unreadable ledger fails the run closed (`BUDGET_UNAVAILABLE`).
   - **On error, timeout or refusal:** it falls back to `llm_fallback_model`.
   - **After a call:** it charges actual usage to the run, the ledger and `billing.usage.recorded`, then re-checks the run cap.
   - **Invalid output:** repaired at most `max_repair_attempts` times, and so is SQL rejected by the validator.

   **Token accounting across a crash and resume never resets:**
   - **Run usage** (`flow_state.usage`) is saved to the run row immediately after every charge, before the step finishes (`on_charged`). On resume it is loaded from the row, so the run cap keeps counting from where the run stopped.
   - **The interrupted call is charged again.** A model call whose step had not finished before the crash is repeated on resume and paid for twice, which matches real spend.
   - **The tenant's daily ledger** (Redis) and the **billing events** are charged per call and are not touched by resume.
   - **The run deadline** is saved when the run first starts, so `RUN_TIMEOUT` does not restart either.
   - `test_token_accounting_survives_a_crash_between_charge_and_step_persist` covers this. Before this fix, usage was saved only with the finished step, so a crash between a paid call and the step being saved dropped that call from the run's usage.

   Prompts carry user text, catalog and results as tagged JSON data blocks, never as instructions. Result rows are never sent to a model; only the result schema is.

5. **Providers.**
   - `anthropic` uses `AsyncAnthropic().messages.parse(output_format=…)`, with defaults `claude-opus-5` → `claude-opus-4-8`.
   - `scripted` is a deterministic offline provider for development, tests and the live DoD flow.
   - `assert_production_safe` refuses `scripted` in staging and prod.

6. **Idempotency-Key (§9.1)** applies to `POST /conversations/{id}/messages` only. The same key with the same conversation, user, content and data source replays the original `202` (`Idempotent-Replayed: true`). The same key with a different body returns `409 IDEMPOTENCY_KEY_REUSED`. It is enforced by the unique constraint `runs (tenant_id, idempotency_key)`. Since ADR 0008, api-gateway also guards every §9 mutating route; this constraint remains the durable layer for run creation.

7. **SSE in api-gateway.** `GET /api/v1/runs/{id}/events` works like this:
   - It subscribes to Redis first, then replays from the orchestrator's internal events route.
   - It de-duplicates by `seq` and fills gaps from the record.
   - It sends heartbeats every 15 s and closes on a `run.*` event.
   - `id` is `seq`, so `Last-Event-ID` resumes the stream.

8. **CrewAI dependency and risk acceptance** (decided by the project owner).
   - `crewai==1.15.22` is pinned. It requires `pydantic<2.13`, so the workspace pydantic floor is `2.12.5`.
   - crewai hard-pins `chromadb~=1.1.0`, which carries advisories PYSEC-2026-311, -3813, -3814 and -3815. CI's `pip-audit` ignores exactly these four IDs.
   - **Correction to the pre-implementation note:** we said the platform would never import chromadb. That is wrong. Importing `crewai` imports `chromadb`.
   - The advisories affect Chroma's HTTP server, which is never started here, and no Chroma client or memory feature is used.
   - `test_a_full_run_opens_no_outbound_connection_and_no_chroma_client` proves that a full run constructs no Chroma client and opens no outbound connection.
   - CrewAI telemetry, tracing and the version check are forced off in the package `__init__` and the Dockerfile.
   - **Expiry:** the four ignores are valid only for the locked versions reviewed here, **crewai 1.15.22 / chromadb 1.1.1**. The CI step "Accepted-advisory expiry (ADR 0006)" fails as soon as either locked version changes. The dependency bump that changes them must re-review the advisories and, in the same change, either update this ADR and the `REVIEWED_*` versions or remove ignores that no longer apply.

## Gaps — not implemented, need a decision

- **The Anthropic provider is not verified against the live API — required before Phase C1 starts** (spec, Phase C1 entry requirement). No API key is available in dev or CI. It is covered only by typed unit tests of the router, and the live flow uses `scripted`. Before C1, run a full A5 DoD run with a real key covering:
  - primary and fallback models;
  - structured output parsing;
  - refusal and `max_tokens` handling;
  - actual token usage matching the ledger.

  Record the result in an ADR.
- ~~**`resolve_semantics` and `analyze_result` (§10) are assigned to Phase A7**~~ Done in Phase A7 ([ADR 0010](0010-phase-a7-semantic-service.md), [ADR 0009](0009-analyze-result-data-exposure.md)).
  - `resolve_semantics` needs the semantic layer.
  - `analyze_result` is the first stage that would show query results to a model, so A7 must record in an ADR, before implementing it, what result data the model may see.
- **`billing.usage.recorded` is published to JetStream `BILLING` but nothing consumes it** until the billing phase. The Redis ledger is the enforcement source.
- ~~**The artifact lives in `runs.flow_state` and ChartSpec in `platform-contracts`** until A6 creates visualization and dashboard services.~~ Resolved in Phase A6 ([ADR 0007](0007-phase-a6-visualization-dashboard.md)): artifacts live in dashboard-service, and the validator in visualization-service.
- **One data source per run.** A tenant with several active sources must pass `data_source_id` (`DATA_SOURCE_SELECTION_REQUIRED`).
- **Cancellation is cooperative.** It takes effect at the next step boundary. A model or query call already in flight finishes, is charged, and is then discarded.
