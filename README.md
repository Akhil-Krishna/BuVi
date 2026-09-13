# buvi — Agentic BI Platform

A governed, multi-tenant, AI-native analytics platform: a chat-first client
experience, a Superset-like developer workspace, and a real admin control plane,
built as independently deployable services in one monorepo.

**The architecture spec is the contract.** Read
[`docs/architecture/Agentic_BI_Platform_Build_Spec.md`](docs/architecture/Agentic_BI_Platform_Build_Spec.md)
before changing anything here. It is the single source of truth for services,
schemas, API contracts, security rules, and build order, and is recorded as the
baseline in [ADR 0001](docs/adr/0001-architecture-baseline.md).

## Status

**Phase A0 (monorepo bootstrap) complete.** No service code, no database
migrations, and no frontend features exist yet. `web/next-app` is a stock
`create-next-app` scaffold and stays that way until Phase B1.

## Stack

Next.js 16 (App Router, TypeScript, React 19) · FastAPI (Python 3.12) ·
CrewAI Flows · uv · PostgreSQL 16 · Redis 7 · NATS JetStream · Keycloak ·
HashiCorp Vault · OpenTelemetry · Docker/Kubernetes · Terraform

## Layout

```
apps/         one FastAPI service per bounded context (Section 3)
web/next-app  the Next.js frontend — the only origin the browser talks to
packages/
  python/     platform-contracts | -observability | -auth | -testing
  ts/         generated OpenAPI client, shared UI primitives
infra/        docker | compose | kubernetes | terraform
contracts/    checked-in OpenAPI specs, event schemas, JSON Schemas
docs/         architecture | adr | runbooks
scripts/      bootstrap, migrate, seed, client generation
```

Every service follows the canonical structure in Section 4.1, with the layer
dependency direction `api -> application -> domain` enforced in CI.

## Getting started

Prerequisites: [uv](https://docs.astral.sh/uv/), Node 20, Docker.

```bash
uv python install 3.12
make sync          # resolve and install every Python workspace member
make web-install   # npm ci in web/next-app
make check         # everything CI runs: lint, type-check, test, both languages
```

`make help` lists all targets. `make up` (local infrastructure), `make migrate`,
`make seed`, and `make dev` become available in Phase A1, when
`infra/compose/docker-compose.dev.yml` and the first service land.

## Build order

Backend first, frontend second — do not reorder:

1. **Track A (A0–A12)** — every backend service plus the CrewAI Flow, proven
   entirely over HTTP/SSE with no browser involved.
2. **Track B (B1–B7)** — the Next.js app, started only after Phase A12's hard
   gate: a green backend suite in CI and a generated TypeScript client committed
   to `packages/ts/api-client`.
3. **Track C (C1–C2)** — production hardening, then optional Superset work.

## Non-negotiables

Section 37 of the spec, enforced on every change:

- Customer database credentials exist only as `secret_ref` pointers into Vault —
  never in frontend code, logs, error responses, or Git.
- No endpoint authorizes on a permission check alone; every endpoint taking a
  resource ID also checks tenant ownership. Cross-tenant IDs return `404`.
- No LLM-generated SQL executes without passing the query-gateway validator.
- Every agent stage hands the next one a typed, schema-validated model — never
  unbounded free text.
- No shared `utils`/`common` dumping grounds. Code lives with the feature that
  owns it, or in a named `packages/python/platform-*` package.
- A service is done when it has an owner, an API contract, health checks, tests,
  and a runbook — not when it runs.
