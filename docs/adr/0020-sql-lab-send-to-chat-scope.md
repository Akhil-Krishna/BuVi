# 0020. SQL Lab's "Send to Chat/Chart" seeds the composer; it does not skip regeneration

## Context

Phase B4's spec text ("Add 'Send to Chat/Chart' from a result set") and DoD ("send a result to
the chart flow in the real UI") leave open exactly what "send" means, since Section 9's chat
surface (`POST /conversations/{id}/messages`) accepts only `content: str` and an optional
`data_source_id` — there is no field for injecting SQL or result rows directly. The shipped
implementation seeds `/chat`'s composer with the same data source and a natural-language prompt
built from the query (`ChatPanel`'s `initialPrompt`/`initialDataSourceId` props), then lets the
real chat pipeline — the full `AnalyticsFlow` — run from a fresh natural-language request. The
model regenerates SQL from scratch; it does not reuse SQL Lab's already-validated query text.

The alternative — a small endpoint that accepts already-validated SQL and skips regeneration —
was investigated directly against `analytics_orchestrator`'s actual Flow
(`infrastructure/flow/analytics_flow.py`, `application/services/run_executor.py`) before deciding
against it for B4. It is not small:

1. **Every downstream stage depends on typed state only the "skipped" stages populate.**
   `_authorize_query` asserts `state.schema_context` (built by `_retrieve_schema`) and checks the
   validated SQL's tables against it — a real Section 7.2 authorization check, not a formality.
   `_analyze_result` and `_build_chart_spec` both assert `state.request` (built by
   `_classify_intent`: title, chart preference, metrics, dimensions). Skipping `classify_intent`
   through `generate_sql` means *synthesizing* correct replacements for `AnalyticsRequest` and a
   full `SchemaContext` (ranked tables, PII-filtered columns, not just names) from the seed SQL,
   not just short-circuiting a `@listen` chain. Getting `schema_context` wrong is a real security
   bug, not a cosmetic one: it is exactly what `_authorize_query` trusts.
2. **The CrewAI Flow has no branch point for this today.** Every step is `@listen(previous_step)`
   in a straight line (`analytics_flow.py`); a conditional skip needs a CrewAI `@router()` step
   and a new persisted state field (`AnalyticsRunState` is versioned, durable flow state —
   Section 18.1 — so this is a schema change, not a local variable).
3. **It changes a spec-documented contract.** Section 32's event table hardcodes the exact
   9-stage sequence (`intent.started` … `run.completed`) the browser's execution trace is built
   around (`ExecutionTrace.tsx`'s `TRACE_STEPS` grouping assumes all stages fire). A skip path
   must decide whether to still emit `intent.*`/`schema.*`/`semantic.*`/`sql.*` events for stages
   that did not really run (misleading) or omit them (a second, different event sequence the
   frontend has to special-case) — either way, Section 11's contract changes, not just the
   backend's internals.
4. **It is new capability, not a bug fix.** Every Track B phase this build has shipped has kept
   Track A frozen except for pre-flagged, narrowly-scoped bug fixes (the B2 `run.cancelled` wire
   event, tracked in ADR 0017 *before* B2 started). This was discovered mid-B4, is not a fix to
   existing behavior, and would touch a security-relevant part of Track A (query authorization)
   without the same design/test rigor Track A's own phases required (Section 37: "No endpoint
   authorizes on a permission check alone without also checking the resource belongs to the
   caller's tenant" — the equivalent per-query check here is `_authorize_query`, and shortcuts
   through it are exactly what this project's own audit discipline (`docs/audit/`) exists to
   catch, not wave through mid-phase).

## Decision

Ship the composer-seed handoff for B4, as already built. Do not add a skip-regeneration endpoint
in this phase.

Track the real feature — an analytics-orchestrator entry point that accepts pre-validated SQL and
a data source, synthesizes a correct `SchemaContext` (from the SQL's actual referenced tables via
`metadata-service`, not guessed) and a minimal `AnalyticsRequest`, runs a CrewAI `@router()` branch
that starts at `validate_sql` instead of `classify_intent`, and defines what its SSE events look
like — as a Phase C1 candidate (alongside the other hardening-track items CLAUDE.md already
carries forward), not a Track B phase. It needs the same design scrutiny as any other change to
`_authorize_query`'s inputs, which Track B's frozen-backend discipline is not the place for.

## Consequences

- SQL Lab's chart handoff costs one full LLM regeneration pass (intent → schema → semantic → SQL)
  the user's query already made unnecessary — slower and more expensive than it needs to be, and
  the regenerated SQL is not guaranteed to be textually identical to what was tested in SQL Lab,
  though it operates over the same catalog and authorization rules either way.
- No security or contract risk taken on: `_authorize_query`'s inputs are exactly what
  Track A already tested, unmodified.
- The regenerated-SQL cost is visible to a developer paying attention (SQL Lab and chat run the
  agent's own model, not necessarily the exact query typed) — worth surfacing if this becomes a
  real user complaint, at which point the Phase C1 item above should be picked up rather than
  patched in Track B.
