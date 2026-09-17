# Agentic BI Platform — Instructions for Claude Code

**Project folder / workspace name: `buvi` (Business Visualization).** The repo is a `uv`
workspace whose root is virtual — it defines the workspace and the shared toolchain, and ships
no code of its own. Deployable units live in `apps/<service>/`, shared libraries in
`packages/python/platform-*`.

Read `docs/architecture/Agentic_BI_Platform_Build_Spec.md` (the v6 spec) before doing anything
in this repo. It is the single source of truth for architecture, schemas, API contracts,
security rules, and the build order. `docs/adr/0001-architecture-baseline.md` records it as the
baseline and lists the spec's known stale cross-references — check that errata table before
chasing a section number that doesn't match its content.
Do not invent services, tables, endpoints, or roles that aren't in that document — if something
is genuinely missing, stop and ask, don't guess.

## Current phase

> Update this line yourself after every completed phase, then commit it.

**Phase A6 (Visualization Service + Dashboard Service, API only) is complete — `apps/visualization-service`
owns the Section 17 ChartSpec validator (pure function over raw JSON, `POST /internal/v1/chart-specs/validate`),
called by the Flow's chart steps and by dashboard-service. `apps/dashboard-service` is the canonical artifact store
(Section 8.9; the Flow writes idempotently under an id derived from the run) and serves `GET /artifacts/{id}`,
`GET /artifacts/{id}/data` (rows via query-gateway `POST /internal/v1/results/read`, `410` after the TTL), dashboards
and tiles (owner-only changes, overrides limited to Section 17 options), publishing `dashboard.tile.pinned`.
`Idempotency-Key` is enforced for every Section 9 mutating route at api-gateway (Redis; ADR 0008). Live flow: `make test-dashboards` (Section 32 Steps A-D as a client user). Decisions and gaps:
`docs/adr/0007-phase-a6-visualization-dashboard.md` (A5: `docs/adr/0006-phase-a5-analytics-orchestrator.md`).
Next up: Phase A7 (Semantic Service). See Section 31 of the build spec.**

**Carried forward (do not drop):**
- **Before Phase C1:** assign a phase to artifact refresh (re-executing expired results) and to artifact versioning (Section 16, which needs a lineage column); ADR 0007.
- **Phase A7:** wire both deferred Flow steps, `resolve_semantics` and `analyze_result`. Decide in an ADR what result data `analyze_result` may show the model before building it.
- **Before Phase C1 starts (required):** test the `anthropic` model provider against the real API with a real key, and record the result in an ADR (spec Phase C1 entry requirement; ADR 0006).
- **Any crewai/chromadb version bump:** re-review the chromadb advisory ignores. CI's "Accepted-advisory expiry (ADR 0006)" step fails until you do.

## Build order (do not violate)

This project is built **backend + CrewAI first, frontend second** (Section 31.0):

1. **Track A (Phases A0–A12)** — every backend microservice + the CrewAI Flow. No frontend
   code is written in this track except the one-time `create-next-app` scaffold in A0.
2. **Track B (Phases B1–B7)** — the Next.js frontend, built only after Track A's exit gate
   (Phase A12) passes: a full automated backend test suite, green in CI, with an OpenAPI-
   generated TypeScript client committed to `packages/ts/api-client`.
3. **Track C (C1–C2)** — production hardening, then optional Superset integration.

Never skip ahead to a later phase. Never start Track B before Phase A12's DoD passes.

## Working on a phase

For each phase:

1. Open the build spec and re-read the exact phase section (e.g. "Phase A4" under Section 31),
   plus any sections it references (e.g. Phase A4 references Sections 4.1, 8.5, 13, 25).
2. Implement exactly what that phase describes — the folder structure (Section 4), the DDL
   (Section 8), the API contract (Section 9), and any code snippets given, are the contract, not
   a suggestion.
3. Write the tests the phase's Definition of Done requires *before* declaring the phase done.
4. Run the full test/lint/type-check suite for anything you touched.
5. Report the DoD checklist explicitly — which items pass, which don't — don't just say "done."
6. Only after DoD passes: update "Current phase" above, commit with a message like
   `feat(phase-a4): query gateway SQL validation + execution`, and stop for review before
   starting the next phase.

## Non-negotiables (Section 37 of the build spec — enforce these on every change)

- No customer database credential ever appears in frontend code, logs, error responses, or
  Git — only `secret_ref` pointers into Vault.
- No endpoint authorizes on a permission check alone without also checking the resource belongs
  to the caller's tenant (Section 7.2). Cross-tenant resource IDs return `404`, not `403`.
- No LLM-generated SQL executes without passing the query-gateway validator (Section 13) first.
- No agent stage passes unbounded free text to the next stage — every hop is a typed, schema-
  validated model (Section 10.3).
- No shared "utils.py" / "common" dumping-ground modules — code lives with the feature that
  owns it, or in a named `packages/python/platform-*` package with a narrow contract.
- Every new service needs: owner, API contract, health checks, tests, and a runbook before it's
  considered done — not just "it runs."

## Model guidance

- Default to Sonnet for routine implementation work (scaffolding, CRUD endpoints, tests, UI).
- Switch to Opus (`/model opus`) for: the SQL validator/allow-list logic (Phase A4), the CrewAI
  Flow structure (Phase A5), the RBAC/RLS authorization layer (Phase A1/A10), or any moment you
  are not confident in the architectural approach before committing to it across services.
- Switch back to Sonnet (`/model sonnet`) once the design is settled and it's implementation work.

## When something in the spec seems wrong or missing

Don't silently improvise. Say what's missing/ambiguous, propose the smallest reasonable fix, and
record the decision as `docs/adr/NNNN-title.md` before proceeding — this keeps the spec and the
codebase from drifting apart as the build progresses.

**The repo copy of the spec is the only copy.** If the spec is edited anywhere else, bring that edit
into `docs/architecture/Agentic_BI_Platform_Build_Spec.md` and commit it, on its own, before any code
change that depends on it. Before implementing a spec change, diff the committed spec so the code
follows the canonical text, not a description of it. Never keep two independently edited copies.
