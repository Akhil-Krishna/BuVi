# 0001 — Architecture baseline

- **Status:** Accepted
- **Date:** 2026-09-13
- **Phase:** A0 (Monorepo bootstrap)
- **Deciders:** Akhil Krishna

## Context

Phase A0 of the build plan requires a baseline ADR recording the authoritative
architecture document for this repository, so that later phases have a fixed
reference point and any drift between the spec and the codebase is a recorded
decision rather than an accident.

## Decision

`docs/architecture/Agentic_BI_Platform_Build_Spec.md` — *Agentic BI Platform,
Build-Ready Engineering Specification (v6)* — is the architectural baseline for
this repository.

Its sections are binding as written:

- Section 2 (roles), Section 3 (services), Section 4 (folder structure),
  Section 7 (authorization), Section 8 (DDL), Section 9 (API contracts),
  Section 13 (query-gateway validation), Section 17 (ChartSpec),
  Section 18.1 (event topics), Section 24 (security checklist),
  Section 31 (phase plan), Section 37 (non-negotiable rules).
- Every "MUST" is a hard constraint. Every "SHOULD" is a strong default that
  requires a superseding ADR to deviate from.
- No service, table, endpoint, or role is added without an ADR in this directory.

The build order is Track A (A0–A12, backend + CrewAI) → Track B (B1–B7,
frontend) → Track C (C1–C2, hardening), with Phase A12 as a hard gate.

## Baseline established by Phase A0

| Concern | Decision |
|---|---|
| Python | CPython 3.12 pinned repo-wide via `.python-version`, `requires-python = ">=3.12,<3.13"` |
| Workspace | `uv` workspace; members `apps/*` and `packages/python/*`; one `uv.lock` committed |
| Workspace root | Virtual (`[tool.uv] package = false`) — defines the workspace, ships no code |
| Python lint/format | Ruff, single `ruff.toml` at the repo root, inherited by all members |
| Python typing | mypy, strict, configured once in the root `pyproject.toml` |
| Python tests | pytest + pytest-asyncio + testcontainers; markers `unit`/`integration`/`contract`/`security`/`system` |
| Frontend | Next.js 16 App Router, React 19, TypeScript, Tailwind CSS 4, own `package.json`/`package-lock.json` |
| Frontend lint/format/test | ESLint (`eslint-config-next` + `eslint-config-prettier`), Prettier, Vitest |
| CI | GitHub Actions `.github/workflows/ci.yml`: lint + type + test + dependency/secret scanning |

## Deviations from the spec text, and why

These are mechanical corrections made during Phase A0. None changes the
architecture; each is recorded here so the spec and the codebase do not drift
silently.

1. **The workspace root is a virtual project.** `uv init --name buvi` had
   produced a distributable root package (`src/buvi/`, `[project.scripts]`,
   `[build-system]`). Section 4 shows no root `src/` tree and describes the root
   `pyproject.toml` purely as the workspace root, so `src/buvi/` was removed and
   `[tool.uv] package = false` set. Every deployable unit is an `apps/<service>/`
   member; every shared library a `packages/python/platform-*` member.

2. **The CI service matrix is discovered at run time.** Section 26's skeleton
   hardcodes all eleven services in the matrix; none exist yet, so that workflow
   could not satisfy Phase A0's own DoD ("CI pipeline runs green on an
   empty-service commit"). A `discover-services` job enumerates `apps/*` and the
   matrix consumes it, which keeps Section 26's per-service shape, stays green
   today, and picks up each new service from Phase A1 onward with no edit to the
   workflow.

3. **CI action versions are current, not the spec's literals.** Section 26 names
   `actions/checkout@v4`, `astral-sh/setup-uv@v3`, `actions/setup-node@v4`;
   this repo uses `@v5`, `@v7`, `@v5` respectively. The spec labels that YAML
   "abbreviated"; pinning to superseded action majors would be a maintenance
   liability, not fidelity.

4. **`pip-audit` is fed an exported requirements file.** Section 26's
   `pip-audit -r apps/**/pyproject.toml` passes a `pyproject.toml` to a flag that
   expects a requirements file. CI instead runs
   `uv export --all-packages --format requirements-txt --no-emit-workspace`
   and audits that, which is the same intent and actually executes. The spec's
   trailing `|| true` is also dropped: Section 24 requires the scan to *block* on
   high/critical findings, so it must be able to fail the build.

5. **The contract jobs are deferred to Phase A2.** Section 26's `contracts` job
   calls `scripts/gen-openapi.sh` and `scripts/diff-contracts.sh`. No service
   exports an OpenAPI document until Phase A2, whose DoD is the first
   `contracts/openapi/api-gateway.json`. Phase A0 explicitly permits CI to "start
   with lint+test only, expand later", so those scripts are written in A2
   rather than stubbed here.

6. **Tailwind CSS 4 has no `tailwind.config.ts`.** Section 5.1 and Phase B1
   instruct that the design tokens be defined in `src/app/globals.css` *and*
   `tailwind.config.ts`. `create-next-app@latest` now scaffolds Tailwind 4, whose
   configuration is CSS-first: there is no `tailwind.config.ts`. Phase B1 must
   therefore define the Section 5.1 palette/type tokens as CSS custom properties
   in an `@theme` block in `src/app/globals.css`. This is a change of file, not
   of tokens — the palette, typography, and layout rules in Section 5.1 are
   unchanged and remain binding.

## Spec errata (cross-references, not architecture)

The v6 text carries stale internal cross-references from earlier revisions.
Recorded here so implementers of later phases follow the pointer to the right
section; the spec body is left as the historical baseline.

| Spec text says | Actually located in |
|---|---|
| §0: "work through Section 25 (Deterministic Build Plan)" | Section 31 |
| §2: permission matrix "Section 9" | Section 7.1 |
| §2: impersonation logging "Section 8.5" | Section 7.3 / Section 8.1 `audit_events` |
| §2: step-up operations "Section 8.6" | Section 7.3 |
| §7.2 heading references, §9 preamble "error envelope Section 22" | Section 21 |
| §1 / §19: physical DB split "Section 20" | Section 19 |
| §5: uv workspace "Section 27.1" | Section 30.1 |
| §5 / §22: observability "Section 23" | Section 22 |
| §5 / §13: testing "Section 24" | Section 25 |
| §5: rate limiting "Section 16" | Section 20 / Section 24 |
| §11 / §18: topic contracts "Section 19" | Section 18.1 |
| §28: containers "Section 26" | Section 28 |
| §1 / §33: Superset "Section 30" | Section 34 |
| §24 / §33: non-negotiable rules "Section 34" | Section 37 |
| §10.2: production DoD "Section 34" | Section 38 |

Also corrected outside the spec: `CLAUDE.md` pointed at
`docs/architecture/build-spec-v6.md`, which does not exist; it now points at
`docs/architecture/Agentic_BI_Platform_Build_Spec.md`.

## Consequences

- Later phases implement against the spec sections listed above, not against
  re-derived designs. A conflict between code and spec is a bug in one of them.
- Any genuine architectural change supersedes this ADR with a new numbered ADR;
  this file is not edited in place except to mark it superseded.
- Phase A0 writes no service code, no frontend feature code, and no database
  migration. `web/next-app` stays at its scaffolded default until Phase B1.
