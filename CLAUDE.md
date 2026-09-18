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

**Phase A9 (MCP Gateway, read-only tools first) is complete -- mcp-gateway (port 8009, schema `mcp`, RLS,
append-only `invocations`): registration with a declared tool manifest and no network contact; approval
(`org_admin` + step-up) that discovers tools over MCP Streamable HTTP and verifies the manifest; read-class tool
grants; invocation with approved -> policy -> grant checks, every attempt recorded and audited, denials published as
`mcp.invocation.denied`, output returned as untrusted and never stored. Section 15 runs at request time: resolve and
check, pin to the address with TLS verified against the hostname, no keep-alive, no redirects, capped JSON/SSE
bodies. The client is tested against the official MCP SDK server in both response modes. Live: `make test-mcp`.
Decisions: `docs/adr/0012-phase-a9-mcp-gateway.md` (A8: 0011, A7: 0010, A6: 0007, idempotency: 0008, A5: 0006).
Next up: Phase A10 (Admin backend, step-up, quotas, WebAuthn -- API only). See Section 31 of the build spec.**

**Carried forward (do not drop):**
- **Phase C1 hardening (required for C1's DoD):** semantic-lookup caching. Cache the approved-definition context and the metadata agent-context packet per tenant/data source with a TTL and invalidation on approve/deprecate/sync; correctness must not depend on the cache (spec Phase C1; ADR 0010).
- **Post-GA backlog (not in Tracks A–C):** Snowflake/BigQuery/Redshift connectors (need vendor sandboxes in CI; spec "Post-GA backlog"; ADR 0011). Also: ratio metrics, metric filters, and multi-table metrics over approved `join_rules`, with join-rule management. Any extension must keep the metric-vs-SQL check exact (parsed), never presence-based (spec "Post-GA backlog"; ADR 0010).
- **Before Phase C1:** assign a phase to artifact refresh (re-executing expired results) and to artifact versioning (Section 16, which needs a lineage column); ADR 0007.
- **Before Phase C1 starts (required):** test the `anthropic` model provider against the real API with a real key, and record the result in an ADR (spec Phase C1 entry requirement; ADR 0006).
- **Before Phase C1 starts (required):** certificate-verified data-source TLS (`verify-full`) for Postgres and MySQL. Add a per-data-source CA bundle, and prove hostname-mismatch and untrusted-CA refusal against TLS-enabled instances in CI (spec Phase C1 entry requirement; ADR 0011).
- **Phase C1 (required):** mcp-gateway/notification-service egress through a dedicated egress proxy with a
  NetworkPolicy, and per-tenant MCP invocation concurrency/rate limits (spec Sections 15, 24; ADR 0012).
- **Phase A10:** MCP server disable/reject, write-tool invocation with per-invocation step-up confirmation, and
  per-user `mcp:manage` for developers (spec Phase A10; ADR 0012).
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
