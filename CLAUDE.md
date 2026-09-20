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

**Phase A12 (Backend completeness gate -- Track A exit) is complete -- `scripts/backend-e2e.sh` (`make backend-e2e`,
CI job "backend e2e (Track A exit gate)") runs contracts + static Section 24 controls, every live DoD flow A1-A12 from the
shared reset, and the live `system` suite against the compose stack. Section 24 is a code registry
(`tests/system/section_24.py`) mapping every bullet to its enforcing tests; supply chain, tenant-deletion cascade and MCP
per-tenant limits are explicit Phase C1 deferrals. `packages/ts/api-client` (`@buvi/api-client`) is generated from
`contracts/openapi/api-gateway.json` by `scripts/gen-client.sh` (drift-checked in CI) and driven live by `test-client`.
Fixed on the way: the gateway contract dropped all query parameters; MFA verify had no per-account limit. Decisions:
`docs/adr/0015-phase-a12-backend-completeness-gate.md` (A11: 0014, A10: 0013, A9: 0012, A8: 0011, A7: 0010,
A6: 0007, idempotency: 0008, A5: 0006).
Next up: Track B, Phase B1 (Real browser auth + design system). The backend is frozen except for bug fixes; Track B
imports `@buvi/api-client`. See Section 31 of the build spec.**

**Carried forward (do not drop):**
- **Before Phase C1 starts (required):** diagnose and fix the intermittent A5 crash-resume stall (1 failure in 15 runs;
  ADR 0014 "Conclusion", ADR 0015). Needs both the root cause named from evidence (a recurrence logs the frames it was
  awaiting; CI keeps the `backend-e2e-logs` artifact) **and** the production equivalents of the dev-only mitigations:
  `idle_in_transaction_session_timeout` and `tcp_keepalives_*` on the platform database, and a `lock_timeout` on
  platform sessions. The trigger (Docker Desktop's port forwarder) is dev-only; the class (a client that vanishes
  without a FIN holds row locks until keepalive detection, 2h by default on Linux) is not.
- **Phase C1 hardening (required for C1's DoD):** semantic-lookup caching. Cache the approved-definition context and the metadata agent-context packet per tenant/data source with a TTL and invalidation on approve/deprecate/sync; correctness must not depend on the cache (spec Phase C1; ADR 0010).
- **Post-GA backlog (not in Tracks A–C):** subscriptions/invoicing (`POST /billing/subscription`, b537537);
  `query.completed` with async query/export execution; storage-bytes metering (ADR 0014). Four-eyes semantic approval as a tenant policy (ADR 0013). Snowflake/BigQuery/Redshift connectors (need vendor sandboxes in CI; spec "Post-GA backlog"; ADR 0011). Also: ratio metrics, metric filters, and multi-table metrics over approved `join_rules`, with join-rule management. Any extension must keep the metric-vs-SQL check exact (parsed), never presence-based (spec "Post-GA backlog"; ADR 0010).
- **Before Phase C1:** assign a phase to artifact refresh (re-executing expired results) and to artifact versioning (Section 16, which needs a lineage column); ADR 0007.
- **Before Phase C1 starts (required):** test the `anthropic` model provider against the real API with a real key, and record the result in an ADR (spec Phase C1 entry requirement; ADR 0006).
- **Before Phase C1 starts (required):** certificate-verified data-source TLS (`verify-full`) for Postgres and MySQL. Add a per-data-source CA bundle, and prove hostname-mismatch and untrusted-CA refusal against TLS-enabled instances in CI (spec Phase C1 entry requirement; ADR 0011).
- **Phase C1 (required):** mcp-gateway/notification-service egress through a dedicated egress proxy with a
  NetworkPolicy, and per-tenant MCP invocation concurrency/rate limits (spec Sections 15, 24; ADR 0012).
- **Phase C1 hardening candidates (ADR 0014):** webhook replay/retry queue and resend of failed emails; keyset
  pagination of identity's `directory/users` (capped at 5000 with `truncated: true`; seats are already exact).
- **Any future suspend path (e.g. SCIM):** must run the Section 6.7 deactivation cascade (share links) like
  `DELETE /admin/users/{id}` does (ADR 0014 follow-up).
- **Every new connector engine:** meet the spec Section 13.1 standing rule. Prove its defenses and its server-identity check against a live instance in CI, never from documentation.
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

## Debugging

For anything that's failing, broken, or erroring — a test, a live flow, a CI run, an unexpected
status code — use the `.claude/skills/backend-debugging/SKILL.md` skill rather than
re-deriving triage from scratch. It's loaded automatically when a task looks like debugging; you
can also invoke it directly if it doesn't trigger. It covers request/run correlation, the shared
error-envelope codes and what they actually mean in this codebase, the live-flow reset/state
pitfalls already hit in A8/A10/A11, known CI-vs-local drift causes, and how to handle a bug that
turns out to be security-relevant rather than a plain defect.

Before opening an investigation, always check this file's "Carried forward" list above — a
failure may be a tracked, already-understood item (the A5 crash-resume stall, the 5,000-user seat
ceiling, etc.), not a new bug worth re-diagnosing from zero.

Fastest local triage, in order:

```bash
docker compose -f infra/compose/docker-compose.dev.yml ps      # every service Up?
brew services stop redis                                       # kills cross-run rate-limit leakage
lsof -ti:8000 | xargs kill 2>/dev/null                          # frees api-gateway's port if squatted
make backend-e2e                                                # the full Track A regression, one command
```

A bug found while debugging is handled exactly like a bug found while building a phase: fix it,
add a regression test, and record it in the current or a new ADR — see "When something in the
spec seems wrong or missing" below. A debugging session does not get a lighter standard of proof
than a phase does.

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
