# Agentic BI Platform — Build-Ready Engineering Specification (v6)

**Status:** Deterministic build spec. Supersedes v5 by adding concrete schemas, contracts,
commands, and a numbered execution plan. Written so a senior engineer *or* a coding AI agent
can implement the system phase-by-phase without needing to make undocumented design decisions.

**Stack lock-in:** Next.js (App Router, TypeScript) · FastAPI (Python 3.12) · CrewAI (Flows) ·
uv (Python package/workspace manager) · PostgreSQL · Redis · Kafka/NATS (event broker) ·
Keycloak (OIDC IdP) · HashiCorp Vault (secrets) · OpenTelemetry · Docker/Kubernetes · Terraform.

---

## 0. How to use this document

This document is organized so that each numbered section is a **self-contained build unit**.
An implementer (human or AI) should work through **Section 25 (Deterministic Build Plan)**
top-to-bottom. Every phase in Section 25 references the exact section of this document that
contains the schema, contract, or code needed for that phase — so no phase requires inventing
behavior that isn't already specified elsewhere in this document.

Rules for whoever builds this:

1. Do not skip ahead. Each phase has a **Definition of Done (DoD)**; do not start the next
   phase until the current phase's DoD passes.
2. Do not invent new services, tables, endpoints, or roles that are not listed here without
   recording the decision as an ADR (`docs/adr/NNNN-title.md`).
3. Every "MUST" in this document is a hard constraint. Every "SHOULD" is a strong default that
   requires an ADR to deviate from.
4. When a section gives a JSON Schema, SQL DDL, or Python signature, treat it as the contract —
   implement to it exactly, then extend if the phase requires more fields (extend, don't rename).

---

## 1. Executive decision

Build a governed, multi-tenant, AI-native analytics platform as a set of independently
deployable microservices in a single monorepo.

- **Frontend:** Next.js App Router (TypeScript) — the only origin the browser talks to.
- **Backend:** FastAPI (Python 3.12) microservices, one per bounded context (Section 3).
- **Agent orchestration:** CrewAI **Flow** (not a free agent swarm) inside one service
  (Analytics Orchestrator). Deterministic code does security, parsing, validation, execution.
- **State:** PostgreSQL is the system of record (one logical database per service-owned schema
  at MVP; splittable to physically separate databases later — see Section 20).
- **Cache/ephemeral/streaming coordination:** Redis.
- **Async workflows / eventing:** Kafka (production) or NATS JetStream (lighter footprint) — the
  spec is broker-agnostic at the code boundary; NATS JetStream is the **default for MVP** because
  it is operationally simpler to self-host than Kafka, and the topic contracts in Section 19 map
  1:1 onto either.
- **External database access:** Only through the Query Gateway. No other service, and no
  browser, ever holds a customer database credential.
- **Identity:** Keycloak (self-hosted OIDC IdP) at MVP; swappable for Entra ID/Okta/Auth0 in
  enterprise deployments because the platform speaks standard OIDC, not a proprietary protocol.
- **Secrets:** HashiCorp Vault (or cloud-native equivalent: AWS Secrets Manager / Azure Key
  Vault / GCP Secret Manager) — never environment-variable plaintext for customer credentials.
- **Observability:** OpenTelemetry → Collector → Prometheus/Grafana + Tempo/Jaeger + Loki.
- **Package/workspace management (Python):** `uv`, Python 3.12 pinned repo-wide.
- **BI reference/optional downstream:** Apache Superset may be embedded or API-integrated later;
  it is never rewritten from Flask to FastAPI (Section 30).

Three first-class product roles — **Client, Developer, Admin** — plus the operational roles
needed to run this safely in production (Section 2).

---

## 2. Roles, personas, and access tiers

v5 named Client/Developer/Admin as the product roles. A production system needs more precision:
who can *break glass*, who can *bill*, who can *only read*, and how **machines** (not humans)
authenticate. The following is the authoritative role list. Do not add roles outside this table
without an ADR — role sprawl is a common source of authorization bugs.

| Role | Scope | Summary | Key permissions (see Section 9 for full matrix) |
|---|---|---|---|
| `platform_super_admin` | Platform-wide (cross-tenant) | Anthropic-side/operator staff only. Used for platform operations, tenant provisioning, break-glass support access. **Never** a normal customer role. | `platform:*`, tenant provisioning, impersonation (audited, time-boxed, Section 8.5) |
| `org_admin` | Tenant | Customer-side administrator. Manages users, roles, connections, MCP servers, policies, billing visibility. | `admin:*` scoped to `tenant_id` |
| `billing_admin` | Tenant | Optional narrower admin: subscription/usage/invoices only, no data/security config access. | `billing:read`, `billing:manage` |
| `developer` | Tenant | Superset-like technical workspace user. | `sql:execute`, `data:manage`, `semantic:manage`, `mcp:manage` (if granted), `run:debug` |
| `client` (a.k.a. `analyst`) | Tenant | Chat-first business user. | `chat:use`, `dashboard:read`, `dashboard:pin`, `artifact:read` |
| `auditor` (read-only) | Tenant | Compliance/security reviewer. Read-only across audit logs, connections metadata, dashboards. Cannot execute SQL, cannot change config. | `audit:read`, `*:read` (no `:write`) |
| `service_account` | Tenant or platform | Non-human principal (CI pipeline, scheduled export, partner integration) authenticated via API key or client-credentials OIDC grant. Always scoped to an explicit permission set, never inherits a human role. | Explicit allow-list per account |
| `guest` (optional, off by default) | Tenant | Time-boxed, read-only shared-dashboard viewer via signed link. No login required. | `dashboard:read` on one dashboard, expiring token |

**Design rules that close common loopholes:**

- A user's role is **never** inferred from a JWT claim alone at the resource level — the JWT
  establishes *who* (subject + tenant + coarse role); every resource read/write additionally
  checks tenant ownership and resource-level policy server-side (Section 9).
- `platform_super_admin` accounts MUST use hardware-key MFA (WebAuthn), MUST be excluded from
  normal login rate-limits bypass, and every action taken under impersonation MUST be logged
  with both the operator identity and the impersonated tenant/user identity (Section 8.5).
- A tenant MUST have at least one `org_admin`; the system MUST refuse to demote/delete the last
  `org_admin` of a tenant.
- Role changes, connection secret changes, MCP approvals, and data exports are **step-up
  operations** — see Section 8.6.

---

## 3. Target microservices (bounded contexts)

| Service | Owns | Does not own | Datastore |
|---|---|---|---|
| **api-gateway** | Public API surface, request correlation, authn handoff, rate limiting, routing, request/response schema enforcement | Business data, agent prompts, SQL credentials | stateless (Redis for rate-limit counters) |
| **identity-service** | Users, orgs, roles, invitations, sessions/IdP integration, API keys, policy mapping | Analytics logic, raw query execution | Postgres schema `identity` |
| **analytics-orchestrator** | Conversations, AnalyticsRuns, CrewAI Flow execution, state transitions, agent coordination, SSE event stream | Direct DB credentials, raw SQL execution | Postgres schema `analytics` |
| **metadata-service** | Connection metadata (non-secret), schema catalog, profiling summaries, lineage, sync jobs | Secret plaintext, query execution | Postgres schema `metadata` |
| **semantic-service** | Metrics, dimensions, entities, approved joins, synonyms, metric definitions | Physical DB credentials | Postgres schema `semantic` |
| **query-gateway** | Connection adapters, SQL parsing/validation, policy injection, execution, result handles, query audit | LLM prompting, chart rendering | Postgres schema `query_gateway` (audit + result handles only; never customer data at rest beyond TTL cache) |
| **visualization-service** | ChartSpec validation, transform contracts, render-safety rules | Database access | stateless (no DB; reads artifact refs) |
| **dashboard-service** | Dashboards, tiles, layout, versions, pinning, sharing links | Agent reasoning | Postgres schema `dashboard` |
| **mcp-gateway** | MCP server registry, tool discovery, authorization, invocation proxying, SSRF controls | Unrestricted arbitrary network access | Postgres schema `mcp` |
| **worker-runtime** | Long-running metadata syncs, profiling, exports, scheduled jobs, notification dispatch | Public API contract | consumes queues; writes to owning services via their APIs, not direct DB |
| **notification-service** | User/system notifications (email, in-app, webhook) | Authorization decisions | Postgres schema `notification` |

**Microservice rule (unchanged from v5, and important):** agents are application-level reasoning
components, not services. Do not turn every CrewAI agent into its own microservice — that adds
distributed-system overhead without an ownership, security, or scaling reason.

---

## 4. Repository strategy and exact folder structures

Monorepo, uv workspace for Python services, separate package.json for the Next.js app.

```
buvi/
├── apps/
│   ├── api-gateway/
│   ├── identity-service/
│   ├── analytics-orchestrator/
│   ├── metadata-service/
│   ├── semantic-service/
│   ├── query-gateway/
│   ├── visualization-service/
│   ├── dashboard-service/
│   ├── mcp-gateway/
│   ├── worker-runtime/
│   └── notification-service/
├── web/
│   └── next-app/
├── packages/
│   ├── python/
│   │   ├── platform-contracts/      # shared Pydantic DTOs + event schemas (generated, versioned)
│   │   ├── platform-observability/  # OTel bootstrap, logging config
│   │   ├── platform-auth/           # JWT/OIDC validation, Principal, permission dependency
│   │   └── platform-testing/        # shared pytest fixtures (test containers, factories)
│   └── ts/
│       ├── api-client/              # generated OpenAPI client
│       └── design-system/           # shared UI primitives (if needed beyond shadcn/ui)
├── infra/
│   ├── docker/                      # per-service Dockerfiles (if not colocated)
│   ├── compose/                     # docker-compose.dev.yml + overrides
│   ├── kubernetes/                  # helm charts / kustomize per service
│   └── terraform/                   # cloud infra: VPC, RDS/CloudSQL, EKS/GKE, secrets, DNS
├── contracts/
│   ├── openapi/                     # exported OpenAPI specs per service, checked in
│   ├── events/                      # JSON Schema / Avro for each Kafka/NATS topic
│   └── json-schema/                 # ChartSpec, AnalyticsRunEvent, etc.
├── docs/
│   ├── architecture/
│   ├── adr/
│   └── runbooks/
├── scripts/                         # bootstrap.sh, migrate-all.sh, seed.sh, gen-client.sh
├── .github/workflows/
├── Makefile
├── pyproject.toml                   # uv workspace root
├── uv.lock
└── README.md
```

### 4.1 Canonical FastAPI service structure (applies to every `apps/<service>/`)

```
apps/query-gateway/
├── pyproject.toml
├── Dockerfile
├── README.md
├── migrations/
│   └── versions/
├── src/
│   └── query_gateway/
│       ├── __init__.py
│       ├── main.py
│       ├── dependencies.py
│       ├── api/
│       │   ├── __init__.py
│       │   └── v1/
│       │       ├── __init__.py
│       │       ├── router.py
│       │       ├── health.py
│       │       └── queries.py
│       ├── application/
│       │   ├── __init__.py
│       │   ├── commands/
│       │   ├── queries/
│       │   └── services/
│       ├── domain/
│       │   ├── __init__.py
│       │   ├── entities/
│       │   ├── value_objects/
│       │   ├── policies/
│       │   └── errors.py
│       ├── infrastructure/
│       │   ├── db/
│       │   │   ├── models.py
│       │   │   ├── session.py
│       │   │   └── repositories/
│       │   ├── connectors/
│       │   │   ├── base.py
│       │   │   ├── postgres.py
│       │   │   ├── mysql.py
│       │   │   └── snowflake.py
│       │   ├── cache/
│       │   ├── messaging/
│       │   └── secrets/
│       ├── core/
│       │   ├── config.py
│       │   ├── logging.py
│       │   ├── security.py
│       │   ├── telemetry.py
│       │   └── http.py
│       └── tests/
│           ├── unit/
│           ├── integration/
│           └── contract/
└── alembic.ini
```

**Dependency direction (enforced by import-linter or ruff isort rules in CI):**

`api -> application -> domain` · `application -> domain + infrastructure interfaces` ·
`infrastructure -> domain contracts` · `core -> importable by all, contains no business use
cases` · `domain -> imports nothing from FastAPI/SQLAlchemy/Redis/CrewAI`.

### 4.2 Next.js production structure

```
web/next-app/
├── src/
│   ├── app/
│   │   ├── (auth)/
│   │   │   ├── login/page.tsx
│   │   │   └── callback/route.ts
│   │   ├── (client)/
│   │   │   ├── chat/page.tsx
│   │   │   ├── dashboards/page.tsx
│   │   │   └── dashboards/[dashboardId]/page.tsx
│   │   ├── (developer)/
│   │   │   ├── sql/page.tsx
│   │   │   ├── data/page.tsx
│   │   │   ├── mcp/page.tsx
│   │   │   └── runs/[runId]/page.tsx
│   │   ├── (admin)/
│   │   │   ├── users/page.tsx
│   │   │   ├── roles/page.tsx
│   │   │   ├── connections/page.tsx
│   │   │   ├── billing/page.tsx
│   │   │   └── audit/page.tsx
│   │   ├── (guest)/
│   │   │   └── share/[token]/page.tsx
│   │   ├── api/
│   │   │   └── [...path]/route.ts     # BFF proxy to api-gateway; injects session token server-side
│   │   ├── layout.tsx
│   │   └── error.tsx
│   ├── features/
│   │   ├── chat/{components,hooks,api.ts,types.ts,schemas.ts}
│   │   ├── dashboards/
│   │   ├── sql-editor/
│   │   ├── data-sources/
│   │   ├── mcp/
│   │   ├── auth/
│   │   └── admin/
│   ├── components/{ui,layout}
│   ├── lib/{api,auth,validation,telemetry}
│   └── types/
├── public/
├── proxy.ts                # early request boundary only — NOT the only authz layer
├── next.config.ts
├── package.json
└── tsconfig.json
```

`proxy.ts` (Next.js's request-boundary convention) is an early gate (e.g., redirect
unauthenticated users). It MUST NOT be the sole authorization layer — every Server
Action/Route Handler/backend call re-checks authorization server-side (Section 9).

---

## 5. Full technology stack matrix

| Layer | Choice | Notes / alternative |
|---|---|---|
| Frontend framework | Next.js App Router, TypeScript, React 19 | — |
| Frontend UI kit | Tailwind CSS + shadcn/ui | avoid hand-rolled design system for MVP; tokens in Section 5.1 |
| Frontend state/data | TanStack Query for server state; minimal client state (Zustand only if needed) | do not put server data in Redux-style global stores |
| Charting | Apache ECharts (via `echarts-for-react` or direct) | model never emits JS, only ChartSpec JSON (Section 17) |
| Backend framework | FastAPI, Python 3.12 | pinned via `.python-version` |
| Agent orchestration | CrewAI (Flows primary, Crews for bounded sub-tasks) | Section 10 |
| ORM / migrations | SQLAlchemy 2.x (async) + Alembic | one Alembic history per service |
| Validation | Pydantic v2 at every API/event boundary | — |
| Primary datastore | PostgreSQL 16 | one cluster, per-service schema at MVP (Section 20) |
| Cache / ephemeral coordination | Redis 7 | rate limiting, SSE fan-out, short TTL cache, distributed locks |
| Event broker | NATS JetStream (MVP default) or Kafka (scale-out) | topic contracts in Section 19 are broker-agnostic |
| Object storage | S3-compatible (MinIO locally, S3/GCS/Azure Blob in cloud) | query result exports, large artifacts |
| Identity provider | Keycloak (self-hosted OIDC) | swappable: Entra ID / Okta / Auth0 |
| Secrets manager | HashiCorp Vault | swappable: AWS/Azure/GCP secret managers |
| SQL parsing/validation | `sqlglot` | AST-level allow-list enforcement, Section 13 |
| HTTP client (service-to-service) | `httpx` (async) | — |
| Observability | OpenTelemetry SDK → Collector → Prometheus + Grafana + Tempo/Jaeger + Loki | Section 23 |
| Python package/workspace mgmt | `uv` | Section 27.1 |
| Lint/format (Python) | Ruff | format + lint in CI |
| Type checking (Python) | mypy (strict on domain/application) | — |
| Testing (Python) | pytest, pytest-asyncio, testcontainers | Section 24 |
| Lint/format (TS) | ESLint + Prettier (or Biome) | — |
| Testing (TS) | Vitest (unit), Playwright (E2E) | — |
| Containers | Docker, multi-stage builds, non-root user | Section 26 |
| Orchestration | Kubernetes (Helm or Kustomize) | Section 22 |
| IaC | Terraform | Section 22 |
| CI/CD | GitHub Actions (or GitLab CI equivalent) | Section 25/26 |
| API docs | OpenAPI (auto from FastAPI) checked into `contracts/openapi/` | contract tests diff against this |
| Email (transactional) | Postmark/SES/SendGrid abstraction behind notification-service | MailHog for local dev |
| Feature flags | Unleash or a simple `feature_flags` table + Redis cache | optional at MVP, required before multi-tenant GA |
| Rate limiting | Redis token-bucket at api-gateway; per-tenant + per-user + per-IP tiers | Section 16 |
| Secrets in CI | GitHub Actions OIDC → cloud provider, no long-lived cloud keys in repo secrets | — |

### 5.1 Frontend visual design system (enterprise BI, not a marketing site)

This is a professional data tool used for hours at a time by analysts and admins — it should
read like Superset, Metabase, Looker, or a trading terminal, not like a landing page or a
generic AI-generated SaaS demo. Treat the following as binding design tokens, not suggestions.

**What to avoid (explicitly banned):**

- No emoji anywhere in the product UI — not in buttons, empty states, toasts, nav labels, or
  status badges. Use icons (Lucide, via shadcn/ui) for anything that needs a visual marker.
- No gradient washes, no glassmorphism, no decorative blur/glow effects, no bouncy/spring
  animations, no confetti or celebratory micro-interactions on routine actions (saving a query is
  not an achievement to celebrate).
- No rounded-everything "SaaS card kit" look (identical `border-radius` + identical soft drop
  shadow on every panel regardless of hierarchy) — data-density and hierarchy come from spacing,
  borders, and type weight, not from cards-on-cards.
- No tracked-out ALL-CAPS eyebrow labels, no middle-dot-joined meta strings, no arrow (`→`)
  appended to every button/link, no warm-cream/terracotta palette, no near-black-with-one-neon-
  accent palette — these are the generic "AI-generated" tells and are exactly what to avoid here.
- Minimize non-functional motion. Loading states use a plain spinner/skeleton, not an animated
  illustration; the SSE execution trace (Section 11) updates by simple fade/state-swap only.

**Palette (blue/white, professional, enterprise-BI):**

| Token | Hex | Use |
|---|---|---|
| `--color-bg` | `#FFFFFF` | primary surface/background |
| `--color-bg-subtle` | `#F5F7FA` | page background behind panels, table zebra striping |
| `--color-border` | `#D9DEE4` | hairline borders, table/panel dividers |
| `--color-text-primary` | `#12213A` | body text, headings |
| `--color-text-secondary` | `#5B6B82` | secondary/meta text, helper copy |
| `--color-primary` | `#1E4FB8` | primary actions, links, active nav, focus states |
| `--color-primary-hover` | `#173D8F` | hover/pressed state of primary |
| `--color-accent` | `#0E7CD6` | data highlights, chart accents, selected states |
| `--color-success` | `#1E8E5A` | success states, positive deltas |
| `--color-warning` | `#B7791F` | warning states |
| `--color-danger` | `#C62828` | errors, destructive actions, negative deltas |

Chart color sequence for multi-series ECharts data uses a controlled blue-forward palette
(`#1E4FB8, #0E7CD6, #5B8DEF, #7FA8E8, #12213A, #5B6B82, #1E8E5A, #B7791F`) rather than the
ECharts default rainbow — consistent with a blue/white brand and easier to read at a glance.

**Typography:** one sans-serif family throughout (Inter or IBM Plex Sans — both read as
"enterprise software," not "marketing site"). No separate display face. Type scale is
restrained: body 14px, section headers 16–18px/semibold, page titles 20–22px/semibold — this is
a dense information tool, not an editorial page. Numeric/tabular data (metric values, SQL, IDs)
uses a monospace face (IBM Plex Mono / JetBrains Mono) only where it genuinely is data, not as a
decorative label treatment.

**Layout principles:**

- Left sidebar navigation (role-scoped per Section 2's roles) + top bar with tenant/user context
  — the same structural pattern Superset, Metabase, and most enterprise BI tools use, because
  it's what this audience already knows how to navigate.
- Dense, left-aligned, grid-based layouts. Tables and charts are the hero content, not hero
  imagery or headlines — there is no "hero section" anywhere in this product.
- Consistent, restrained spacing scale (4/8/12/16/24/32px) rather than generous marketing-site
  whitespace; screen real estate favors showing more data over decorative breathing room.
- Borders and hairlines (`--color-border`) delineate panels rather than shadows; reserve shadow
  only for genuinely elevated surfaces (dropdowns, modals, popovers).
- Empty and error states are direct and instructive ("No data sources connected yet. Add one to
  start querying." / "Query failed: syntax error near line 4.") — informative, not cute, no
  illustrations required.

**Implementation:** define these tokens as CSS variables / a Tailwind theme extension in
`web/next-app` once, in `src/app/globals.css` + `tailwind.config.ts`, during Phase B1 (Section
31) — every component pulls from the token set rather than hardcoding colors, so the palette
stays consistent as Track B builds out the chat, SQL editor, dashboard, and admin surfaces.

---

## 6. Authentication: complete production design

Authentication and authorization are separate concerns. The platform uses a standards-based
OIDC identity provider, never a hand-rolled token protocol.

### 6.1 Browser login flow (OIDC Authorization Code + PKCE, BFF pattern)

1. User opens the Next.js app. Unauthenticated → `proxy.ts` redirects to `(auth)/login`.
2. Next.js server route builds the OIDC authorization request (PKCE `code_verifier` stored
   server-side in a short-lived, HttpOnly, Secure cookie) and redirects the browser to Keycloak.
3. User authenticates at Keycloak (password, or upstream SSO if the tenant has SAML/OIDC
   federation configured — Section 6.4).
4. Keycloak redirects to `(auth)/callback/route.ts` with an authorization code.
5. The Next.js server (not the browser) exchanges the code for tokens directly with Keycloak's
   token endpoint. The response (access token, refresh token, ID token) never touches browser
   JavaScript.
6. An **application session** is created: a random opaque session token whose SHA-256 is stored
   in `identity.sessions.token_hash` (Section 8.1), mapped to the token set, and a browser cookie
   containing only that token — never the session row id —
   `HttpOnly`, `Secure`, `SameSite=Lax` (or `Strict` for admin routes), scoped to the app path.
7. All subsequent browser requests go only to the Next.js origin. The BFF looks up the session,
   attaches a short-lived access token (or performs on-behalf-of token exchange) when calling
   `api-gateway`.
8. `api-gateway` validates issuer, audience, signature, expiry, and required claims (`tenant_id`,
   `roles`) on every request before routing.
9. Refresh happens server-side transparently when the access token nears expiry; if the refresh
   token is expired/revoked, the BFF clears the session and redirects to login.

### 6.2 Why not a localStorage JWT

Long-lived bearer tokens in `localStorage`/`sessionStorage` are directly readable by any
injected script if the app has an XSS bug. The HttpOnly-cookie + BFF pattern removes that
attack surface for the token itself. CSRF defenses (double-submit token or `SameSite` +
origin/referer checks) are still required on state-changing requests, because cookies are
sent automatically by the browser.

### 6.3 Internal (service-to-service) authentication

- Do not trust "inside the cluster." Each service validates a workload identity.
- MVP: short-lived service JWTs issued by identity-service's client-credentials grant, scoped
  with an explicit `aud` per callee and narrow `scope` claims (e.g. `scope=query-gateway:execute`).
- Growth: mTLS via a service mesh (Istio/Linkerd) for transport-level mutual auth, JWT for
  fine-grained scope — defense in depth, not either/or.
- The gateway is not the only trust boundary; high-value services (query-gateway, mcp-gateway,
  identity-service) re-validate the caller's service identity and scope independently.

### 6.4 Enterprise SSO / federation

- `org_admin` can configure an upstream IdP (SAML 2.0 or OIDC) per tenant in Keycloak as an
  Identity Broker realm/alias. Users authenticate against their corporate IdP; Keycloak issues
  the platform's own tokens downstream — the rest of the system never needs to know the
  customer's IdP details.
- Just-in-time (JIT) provisioning: first successful federated login creates the user record with
  a default role (`client`) unless SCIM provisioning is configured (Section 6.7).

### 6.5 Password & credential hygiene (for the local-password fallback realm)

- Argon2id for password hashing (OWASP Password Storage Cheat Sheet default recommendation).
- Minimum length 12, no forced periodic rotation, breached-password check (e.g. HaveIBeenPwned
  k-anonymity API) at registration/change time.
- Account lockout: exponential backoff after 5 failed attempts per account+IP pair, tracked in
  Redis; never lock indefinitely without an unlock path (avoid a DoS-via-lockout loophole).
- Email verification required before first login completes for local-password accounts.
- Password reset: single-use, time-boxed (15 min) signed token delivered by email; invalidate
  all other active sessions on password change.

### 6.6 Multi-factor authentication (MFA)

- TOTP (authenticator app) MUST be available to every role.
- WebAuthn/hardware keys MUST be available and MUST be **required** for `platform_super_admin`
  and SHOULD be required (tenant-configurable policy) for `org_admin`.
- MFA **reset/removal** is step-up-protected and audited (Section 7.3). MFA **first enrollment**
  requires only a valid, authenticated session — a user with no factor yet cannot satisfy a
  step-up check, so the contradiction is resolved by treating first enrollment as the exception:
  it establishes the factor, it doesn't need one. Every subsequent MFA change does require
  step-up using the now-existing factor.

### 6.7 User lifecycle: invitation, SCIM, deprovisioning

- `org_admin` invites a user by email + role. Invitation = single-use signed token, 7-day
  expiry, stored in `identity.invitations`.
- **Invitation acceptance is a trust boundary, not just a token check.** The token alone does not
  prove which account is accepting it — accepting the invitation must additionally verify that
  the authenticated IdP identity's email matches the invited email exactly (case-insensitive),
  otherwise a stolen/leaked invitation link could be bound to an attacker-controlled account. Once
  real browser OIDC login exists (Section 31 Track B, Phase B1), invitation acceptance MUST only
  be reachable via a completed real IdP login — never accept an invitation token alongside a
  caller-asserted identity from any other auth path.
- Enterprise tenants MAY connect SCIM 2.0 for automated provisioning/deprovisioning from their
  HR/IdP system — deprovisioning MUST revoke all active sessions and API keys immediately, not
  on next token expiry.
- Deleting/deactivating a user MUST cascade: revoke sessions, revoke API keys owned by that user,
  reassign or archive resources per tenant retention policy, write an audit event.

### 6.8 API keys / service accounts

- `service_account` principals authenticate via `client_id`/`client_secret` (OIDC client
  credentials grant) or a platform-issued API key (`Authorization: Bearer sk_live_...`).
- API keys are shown once at creation, stored hashed (not reversible) in
  `identity.api_keys.secret_hash`, support scoped permissions, an optional expiry, and can be
  revoked instantly. Rotate-without-downtime is supported (two active keys during rotation).
- Every API key request is rate-limited and audited exactly like a human session.

### 6.9 Session & device management

- Users can view and revoke their own active sessions (`identity.sessions`: device label, IP,
  user agent, created_at, last_seen_at, expires_at).
- `org_admin` can force-revoke all sessions for a user in their tenant (e.g., offboarding,
  suspected compromise). `platform_super_admin` can do this cross-tenant.
- Idle session timeout (default 12h) and absolute session lifetime (default 7d) are both
  enforced server-side, independent of the identity provider's own token lifetimes.

---

## 7. Authorization: RBAC + ABAC, deny-by-default

RBAC gives coarse product permissions; tenant/resource attributes give ownership and scope.
"Has `sql:execute`" is not equivalent to "may execute SQL against connection `conn_123`."
Authorization always checks **both** layers, server-side, on every protected operation.

### 7.1 Full permission matrix

| Permission | `client` | `developer` | `org_admin` | `billing_admin` | `auditor` |
|---|---|---|---|---|---|
| `chat:use` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `dashboard:read` | ✅ (tenant-scoped) | ✅ | ✅ | ❌ | ✅ |
| `dashboard:pin` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `dashboard:share` | tenant policy | ✅ | ✅ | ❌ | ❌ |
| `artifact:read` | ✅ (approved data only) | ✅ | ✅ | ❌ | ✅ |
| `sql:execute` | ❌ | ✅ (per-connection grant) | ✅ | ❌ | ❌ |
| `data:manage` (connections) | ❌ | ✅ (create/test), approval by admin for prod | ✅ | ❌ | ❌ (reads metadata via `catalog:read`) |
| `catalog:read` (connection metadata, schema catalog) | ❌ | ✅ | ✅ | ❌ | ✅ |
| `semantic:manage` | ❌ | ✅ | ✅ | ❌ | ❌ |
| `mcp:manage` | ❌ | ✅ (if granted) | ✅ (approve servers) | ❌ | ❌ |
| `run:debug` | ❌ | ✅ | ✅ | ❌ | ✅ (read-only) |
| `user:manage` | ❌ | ❌ | ✅ | ❌ | ❌ |
| `role:manage` | ❌ | ❌ | ✅ | ❌ | ❌ |
| `policy:manage` | ❌ | ❌ | ✅ | ❌ | ❌ |
| `billing:read` | ❌ | ❌ | ✅ | ✅ | ❌ |
| `billing:manage` | ❌ | ❌ | ✅ | ✅ | ❌ |
| `audit:read` | ❌ | partial (own runs) | ✅ | ❌ | ✅ |

`platform_super_admin` has `platform:*` (cross-tenant) and is excluded from this per-tenant
table by design. `service_account` permissions are explicit allow-lists set at creation, never
derived from this table.

### 7.2 Authorization dependency (FastAPI)

```python
from dataclasses import dataclass
from fastapi import Depends, HTTPException, status

@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    permissions: frozenset[str]
    auth_method: str          # "session" | "api_key" | "service_jwt"
    mfa_verified: bool
    session_id: str | None = None

def require_permission(permission: str):
    async def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if permission not in principal.permissions:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return principal
    return dependency

def require_resource_owner(load_resource_tenant_id):
    """Second-layer check: resource must belong to principal.tenant_id.
    `load_resource_tenant_id` is an injected callable that loads the resource's tenant_id
    by path parameter, WITHOUT loading the full object into a trust boundary that skips this
    check. Use this on every endpoint that takes a resource id in the path."""
    async def dependency(
        principal: Principal = Depends(get_principal),
        resource_tenant_id: str = Depends(load_resource_tenant_id),
    ) -> Principal:
        if resource_tenant_id != principal.tenant_id and "platform:*" not in principal.permissions:
            # Return 404, not 403, for cross-tenant resource IDs — do not confirm existence (BOLA/IDOR defense)
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
        return principal
    return dependency
```

**Rule that closes the most common loophole (BOLA/IDOR):** a permission check alone
(`require_permission("dashboard:read")`) is never sufficient on any endpoint that takes a
resource ID. Always compose it with a resource-tenant check. Return `404` (not `403`) for
cross-tenant IDs so attackers cannot enumerate valid resource IDs by response code.

### 7.3 Step-up authorization (sensitive operations)

The following operations require a **recent** (≤5 min) MFA verification or re-auth, in addition
to normal permission checks, and are always written to the audit log with before/after state:

- Adding or changing a database connection's credentials.
- Approving an MCP server or granting a write-capable MCP tool.
- Role changes (grant/revoke), user deletion, tenant deletion.
- Enabling/disabling MFA on another user's account, forced session revocation.
- Exporting or downloading raw query results above a configurable row/byte threshold.
- Rotating or revealing (masked, last-4-only) API keys.

**How step-up is proven (Phase A10).** The session records the method of its last MFA check
(`totp` or `webauthn`). identity-service decides whether a caller must use WebAuthn: always for
`platform_super_admin` (Section 6.6), and for `org_admin` when the tenant policy says so. It puts
that decision in the `Principal`. A step-up done with the wrong method is not fresh, so the
gateway and every service enforce the rule the same way. The refusal is
`403 STEP_UP_REQUIRED` with `details.method`. The export threshold is configurable
(`export_step_up_rows`, default 10,000); a request or response above it needs step-up.

### 7.4 Frontend authorization is UX-only

`proxy.ts` route grouping and client-side role checks control what's *shown*, never what's
*allowed*. Every Server Action and every backend endpoint re-checks Sections 7.1–7.3
independently. This is the single most important rule in this document — violating it is the
#1 way real products get BOLA/broken-access-control incidents (OWASP API Security Top 10).

---

## 8. Data model — authoritative DDL per service schema

All tables: `id UUID PRIMARY KEY DEFAULT gen_random_uuid()` unless noted, `created_at
TIMESTAMPTZ NOT NULL DEFAULT now()`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()` (maintained
by trigger). All tenant-owned tables carry `tenant_id UUID NOT NULL` and an index on
`(tenant_id, ...)` for every list/filter query. Use `citext` for emails. Use row-level security
(Postgres RLS) as a **defense-in-depth** layer in addition to application-level tenant checks —
not a replacement for them.

### 8.1 `identity` schema (owner: identity-service)

```sql
CREATE TABLE identity.tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','deleted')),
    plan TEXT NOT NULL DEFAULT 'trial',
    data_region TEXT NOT NULL DEFAULT 'us',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE identity.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id),
    idp_subject TEXT NOT NULL,            -- Keycloak "sub" claim
    email CITEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'invited' CHECK (status IN ('invited','active','suspended','deactivated')),
    mfa_enabled BOOLEAN NOT NULL DEFAULT false,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, email)
);
CREATE INDEX idx_users_tenant ON identity.users(tenant_id);
CREATE UNIQUE INDEX idx_users_idp_subject ON identity.users(idp_subject);

CREATE TABLE identity.roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES identity.tenants(id),  -- NULL for platform-level roles
    key TEXT NOT NULL,                                -- 'org_admin','developer','client', etc.
    is_system BOOLEAN NOT NULL DEFAULT true,
    UNIQUE NULLS NOT DISTINCT (tenant_id, key)
    -- plain UNIQUE treats every NULL tenant_id as distinct, which would allow duplicate
    -- platform-level (tenant_id IS NULL) roles; NULLS NOT DISTINCT (Postgres 15+) closes that.
);

CREATE TABLE identity.user_roles (
    user_id UUID NOT NULL REFERENCES identity.users(id) ON DELETE CASCADE,
    role_id UUID NOT NULL REFERENCES identity.roles(id) ON DELETE CASCADE,
    granted_by UUID REFERENCES identity.users(id),
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE identity.invitations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id),
    email CITEXT NOT NULL,
    role_key TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    invited_by UUID NOT NULL REFERENCES identity.users(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','revoked','expired')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE identity.sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),      -- internal correlation id only; NEVER
                                                          -- accepted as a bearer credential
    token_hash TEXT NOT NULL UNIQUE,                     -- SHA-256 of the opaque session token;
                                                          -- the raw token is returned to the
                                                          -- caller once at creation and is never
                                                          -- stored — same discipline as api_keys
                                                          -- below. A DB read (backup, replica,
                                                          -- broad-access tooling) must not yield
                                                          -- a usable credential.
    user_id UUID NOT NULL REFERENCES identity.users(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id),
    device_label TEXT,
    ip_address INET,
    user_agent TEXT,
    idp_refresh_token_ref TEXT NOT NULL,   -- reference into Vault, never the raw token
    mfa_verified_method TEXT CHECK (mfa_verified_method IN ('totp','webauthn')),  -- A10
    webauthn_challenge TEXT,               -- A10: pending WebAuthn challenge, single-use
    webauthn_challenge_expires_at TIMESTAMPTZ,
    mfa_verified_at TIMESTAMPTZ,           -- set on successful MFA check; step-up (Section 7.3)
                                            -- requires now() - mfa_verified_at <= 5 minutes
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);
CREATE INDEX idx_sessions_user ON identity.sessions(user_id);

-- Phase A10: tenant policies (Section 3, "policy mapping"). One row per tenant; a missing
-- row means every default (the most restrictive setting).
CREATE TABLE identity.tenant_policies (
    tenant_id UUID PRIMARY KEY REFERENCES identity.tenants(id),
    client_can_share_dashboards BOOLEAN NOT NULL DEFAULT false,  -- Section 7.1 "tenant policy"
    developer_can_manage_mcp BOOLEAN NOT NULL DEFAULT false,     -- Section 7.1 "(if granted)"
    org_admin_requires_webauthn BOOLEAN NOT NULL DEFAULT false,  -- Section 6.6 SHOULD
    updated_by UUID REFERENCES identity.users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE identity.mfa_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id),  -- tenant-owned: RLS (Section 19)
    user_id UUID NOT NULL REFERENCES identity.users(id) ON DELETE CASCADE,
    method TEXT NOT NULL CHECK (method IN ('totp','webauthn')),
    secret_ref TEXT NOT NULL,          -- Vault path; the TOTP secret / WebAuthn credential
                                        -- material itself never lives in Postgres
    label TEXT,                         -- e.g. device name for WebAuthn keys
    confirmed_at TIMESTAMPTZ,           -- set when the user first proves possession (e.g. a valid
                                        -- TOTP code); an unconfirmed row is not an active factor
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,           -- last accepted use; for TOTP also the replay guard: a code
                                        -- from the same or an earlier 30s step is rejected
    revoked_at TIMESTAMPTZ
);
CREATE INDEX idx_mfa_credentials_user ON identity.mfa_credentials(user_id);
-- At most one active TOTP factor per user; WebAuthn users may register several keys.
CREATE UNIQUE INDEX idx_mfa_credentials_one_totp ON identity.mfa_credentials(user_id)
    WHERE method = 'totp' AND revoked_at IS NULL;
-- identity.users.mfa_enabled is a derived/cached flag (true if any non-revoked, confirmed row
-- exists here), not the source of truth.

CREATE TABLE identity.api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id),
    owner_user_id UUID REFERENCES identity.users(id),   -- NULL for pure service accounts
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,          -- constant scheme prefix + random chars for display/
                                        -- lookup disambiguation, e.g. 'sk_live_4f9a2c' — the
                                        -- scheme constant alone ('sk_live_') is identical across
                                        -- every key and cannot distinguish them in a UI list
    secret_hash TEXT NOT NULL,         -- argon2id hash of the full key
    scopes TEXT[] NOT NULL DEFAULT '{}',
    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_by UUID NOT NULL REFERENCES identity.users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE identity.audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,                    -- NULL for platform-level events
    actor_user_id UUID,
    actor_type TEXT NOT NULL DEFAULT 'user' CHECK (actor_type IN ('user','service_account','system')),
    acting_as_tenant_id UUID,          -- set during platform_super_admin impersonation
    event_type TEXT NOT NULL,          -- 'user.role_changed','connection.secret_rotated', ...
    resource_type TEXT,
    resource_id TEXT,
    before_state JSONB,
    after_state JSONB,
    request_id TEXT,
    ip_address INET,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_tenant_time ON identity.audit_events(tenant_id, created_at DESC);
-- Audit events are append-only: revoke UPDATE/DELETE at the DB role level for the app user.
```

### 8.2 `metadata` schema (owner: metadata-service)

```sql
CREATE TABLE metadata.data_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    name TEXT NOT NULL,
    engine TEXT NOT NULL CHECK (engine IN ('postgres','mysql','snowflake','bigquery','redshift')),
    host_label TEXT NOT NULL,          -- sanitized display value only, never full DSN
    database_name TEXT NOT NULL,
    allowed_schemas TEXT[] NOT NULL DEFAULT '{}',
    capabilities JSONB NOT NULL DEFAULT '{}',
    secret_ref TEXT NOT NULL,          -- Vault path, e.g. 'secret/data/tenants/<id>/datasources/<id>'
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','error','disabled')),
    last_sync_at TIMESTAMPTZ,
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_datasources_tenant ON metadata.data_sources(tenant_id);

-- Phase A10: per-connection `sql:execute` grants (Section 7.1). `org_admin` needs none.
CREATE TABLE metadata.data_source_grants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    data_source_id UUID NOT NULL REFERENCES metadata.data_sources(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    granted_by UUID NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (data_source_id, user_id)
);

CREATE TABLE metadata.schema_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    data_source_id UUID NOT NULL REFERENCES metadata.data_sources(id) ON DELETE CASCADE,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum TEXT NOT NULL
);

CREATE TABLE metadata.tables (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    data_source_id UUID NOT NULL REFERENCES metadata.data_sources(id) ON DELETE CASCADE,
    schema_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    description TEXT,
    row_count_estimate BIGINT,
    is_visible_to_agent BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (data_source_id, schema_name, table_name)
);

CREATE TABLE metadata.columns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    table_id UUID NOT NULL REFERENCES metadata.tables(id) ON DELETE CASCADE,
    column_name TEXT NOT NULL,
    data_type TEXT NOT NULL,
    is_pii BOOLEAN NOT NULL DEFAULT false,
    description TEXT,
    sample_values JSONB,
    UNIQUE (table_id, column_name)
);

CREATE TABLE metadata.relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    from_column_id UUID NOT NULL REFERENCES metadata.columns(id) ON DELETE CASCADE,
    to_column_id UUID NOT NULL REFERENCES metadata.columns(id) ON DELETE CASCADE,
    relationship_type TEXT NOT NULL DEFAULT 'fk' CHECK (relationship_type IN ('fk','inferred'))
);
```

### 8.3 `semantic` schema (owner: semantic-service)

```sql
CREATE TABLE semantic.metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    name TEXT NOT NULL,               -- "Revenue"
    description TEXT,
    expression TEXT NOT NULL,          -- v1 grammar: AGG([DISTINCT] column), e.g. "SUM(amount)"
    default_grain TEXT,
    base_table_id UUID NOT NULL,       -- references metadata.tables.id (cross-service; store id only)
    synonyms TEXT[] NOT NULL DEFAULT '{}',
    created_by UUID NOT NULL,
    approved_by UUID,
    approved_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','deprecated')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE semantic.dimensions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    name TEXT NOT NULL,
    column_id UUID NOT NULL,           -- references metadata.columns.id
    synonyms TEXT[] NOT NULL DEFAULT '{}',
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE semantic.join_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    left_table_id UUID NOT NULL,
    right_table_id UUID NOT NULL,
    on_expression TEXT NOT NULL,
    is_approved BOOLEAN NOT NULL DEFAULT false
);
```

**Metric expressions (v1).** `expression` is not free SQL: it must match
`SUM|AVG|MIN|MAX|COUNT( [DISTINCT] column )`. `column` is a column of the metric's base table
(optionally written `table.column`); it must exist in the catalog, must not be PII, and the table
must be visible to agents. `SUM`/`AVG` require a numeric column. semantic-service checks this at
write time through metadata-service. Only `approved` metrics reach the Flow, and the SQL built from
them is still validated by query-gateway (Section 13). Ratio metrics, metric filters and
multi-table metrics (approved `join_rules`) are **post-GA backlog** (Section 31, "Post-GA backlog"):
`join_rules` is created but unused until then, and the v1 grammar is the contract for GA.

### 8.4 `analytics` schema (owner: analytics-orchestrator)

```sql
CREATE TABLE analytics.conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    created_by UUID NOT NULL,
    title TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE analytics.messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    conversation_id UUID NOT NULL REFERENCES analytics.conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content TEXT NOT NULL,
    run_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE analytics.runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    conversation_id UUID NOT NULL REFERENCES analytics.conversations(id),
    requested_by UUID NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','waiting_query','completed','failed','cancelled')),
    current_stage TEXT,
    flow_state JSONB NOT NULL DEFAULT '{}',   -- CrewAI Flow persisted state (Section 10)
    error_code TEXT,
    idempotency_key TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX idx_runs_tenant_status ON analytics.runs(tenant_id, status);

CREATE TABLE analytics.run_events (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES analytics.runs(id) ON DELETE CASCADE,
    seq INT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('started','completed','failed')),
    message TEXT NOT NULL,             -- user-safe only, never raw chain-of-thought
    artifact_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, seq)
);

-- Phase A11: one row per `billing.usage.recorded` event, written by worker-runtime through
-- analytics-orchestrator's internal API. The producer's event_id makes redelivery harmless.
-- `/billing/usage` sums them per period.
CREATE TABLE analytics.usage_records (
    event_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    metric TEXT NOT NULL,          -- llm_input_tokens | llm_output_tokens | query_execution_ms
    quantity BIGINT NOT NULL CHECK (quantity >= 0),
    model TEXT,
    stage TEXT,
    run_id UUID,
    occurred_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_usage_records_tenant_time ON analytics.usage_records(tenant_id, occurred_at);
```

### 8.5 `query_gateway` schema (owner: query-gateway)

```sql
CREATE TABLE query_gateway.query_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    run_id UUID,
    data_source_id UUID NOT NULL,
    requested_by TEXT NOT NULL,        -- user_id or service principal id
    purpose TEXT NOT NULL,             -- 'analytics_run' | 'sql_editor' | 'export'
    sql_text TEXT NOT NULL,
    sql_hash TEXT NOT NULL,
    validation_result JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('validated','rejected','running','succeeded','failed','timeout')),
    row_count INT,
    bytes_returned BIGINT,
    duration_ms INT,
    result_handle TEXT,                -- object storage ref, TTL-bound
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_qe_tenant_time ON query_gateway.query_executions(tenant_id, created_at DESC);
```

### 8.6 `dashboard` schema (owner: dashboard-service)

```sql
CREATE TABLE dashboard.dashboards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    name TEXT NOT NULL,
    owner_id UUID NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','tenant','link')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dashboard.tiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    dashboard_id UUID NOT NULL REFERENCES dashboard.dashboards(id) ON DELETE CASCADE,
    artifact_id UUID NOT NULL,          -- references analytics artifact (Section 16)
    chart_spec_version INT NOT NULL,
    position JSONB NOT NULL,            -- {x,y,w,h}
    overrides JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dashboard.share_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    dashboard_id UUID NOT NULL REFERENCES dashboard.dashboards(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,
    created_by UUID NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dashboard.artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    conversation_id UUID,
    run_id UUID,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',   -- user-safe one-liner returned by GET /artifacts/{id} (Section 9.1)
    semantic_query JSONB NOT NULL,
    source_refs JSONB NOT NULL DEFAULT '[]',
    validated_sql TEXT NOT NULL,
    query_result_ref TEXT NOT NULL,
    result_schema JSONB NOT NULL,
    chart_spec JSONB NOT NULL,
    refresh_policy JSONB NOT NULL DEFAULT '{"mode":"manual"}',
    created_by UUID NOT NULL,
    version INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 8.7 `mcp` schema (owner: mcp-gateway)

```sql
CREATE TABLE mcp.servers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    name TEXT NOT NULL,
    endpoint_url TEXT NOT NULL,
    auth_secret_ref TEXT,
    status TEXT NOT NULL DEFAULT 'pending_approval'
        CHECK (status IN ('pending_approval','approved','disabled','rejected')),
    approved_by UUID,
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mcp.tools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    server_id UUID NOT NULL REFERENCES mcp.servers(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    tool_class TEXT NOT NULL CHECK (tool_class IN ('read_metadata','read_data','external_read','write','admin')),
    default_policy TEXT NOT NULL DEFAULT 'deny' CHECK (default_policy IN ('allow','require_grant','deny')),
    UNIQUE (server_id, tool_name)
);

CREATE TABLE mcp.tool_grants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    tool_id UUID NOT NULL REFERENCES mcp.tools(id) ON DELETE CASCADE,
    grantee_role TEXT,                  -- role key, or NULL if grantee_user_id set
    grantee_user_id UUID,
    granted_by UUID NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mcp.invocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    tool_id UUID NOT NULL,
    invoked_by TEXT NOT NULL,
    run_id UUID,
    request_payload JSONB NOT NULL,
    response_status TEXT NOT NULL CHECK (response_status IN ('ok','denied','error')),
    response_summary TEXT,
    duration_ms INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 8.8 `notification` schema (owner: notification-service)

```sql
CREATE TABLE notification.notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    user_id UUID NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('in_app','email','webhook')),
    template_key TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','failed','read')),
    event_key TEXT NOT NULL,            -- source event (stream:sequence); redelivery is a no-op
    sent_at TIMESTAMPTZ,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_key, user_id, channel)
);
CREATE INDEX idx_notifications_inbox ON notification.notifications(tenant_id, user_id, created_at DESC)
    WHERE channel = 'in_app';

CREATE TABLE notification.webhook_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    url TEXT NOT NULL,
    event_types TEXT[] NOT NULL,
    signing_secret_ref TEXT NOT NULL,   -- HMAC signing secret, Vault-backed
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    created_by UUID NOT NULL,           -- Phase A11: webhook delivery rows are recorded against it
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

A webhook delivery is a `notifications` row with `channel = 'webhook'`, `user_id` = the
subscription's `created_by`, `template_key` = the event type, and the subscription id, attempt
count and failure reason in `payload`.

### 8.9 Entity ownership summary

| Entity | Owner service | Tenant scoped? |
|---|---|---|
| Tenant / User / Role / Session / API Key / Audit Event | Identity | Org root / yes |
| DataSource / SchemaSnapshot / Table / Column / Relationship | Metadata | Yes |
| Metric / Dimension / JoinRule | Semantic | Yes |
| Conversation / Message / AnalyticsRun / RunEvent | Analytics Orchestrator | Yes |
| QueryExecution | Query Gateway | Yes |
| AnalyticsArtifact | Dashboard (canonical store) | Yes |
| Dashboard / Tile / ShareLink | Dashboard | Yes |
| MCPServer / Tool / ToolGrant / Invocation | MCP Gateway | Yes |
| Notification / WebhookSubscription | Notification | Yes |

---

## 9. API contract catalog (api-gateway public surface, `/api/v1`)

All endpoints require a valid session or API key unless marked **public**. All list endpoints
are paginated (`?cursor=`, `?limit=`, default 50, max 200). All mutating endpoints accept an
optional `Idempotency-Key` header. All responses use the error envelope in Section 22.

**Idempotency-Key (enforced once, at api-gateway):**
- **Key format.** 1–255 printable ASCII characters. Records are scoped to tenant + principal + key
  and kept for 24 h.
- **Fingerprint.** Method, path, query and body bytes. The same key with a different request is
  `409 IDEMPOTENCY_KEY_REUSED`, on any route.
- **Authorization first.** Authentication, permission, step-up and rate limits run on every
  request before any replay; a replay never bypasses them.
- **Replay.** The first final response (2xx, or a deterministic 4xx) is replayed with
  `Idempotent-Replayed: true`.
- **Never recorded:** 5xx, 401, 403, 408, 409, 425 and 429. The client may retry those.
- **Concurrency.** A concurrent request with the same key is
  `409 IDEMPOTENCY_REQUEST_IN_PROGRESS` with `Retry-After`.
- **Responses that must not be stored.** A one-time secret (API key, MFA enrollment, webhook
  signing secret, share-link token), customer query rows (`/sql/execute`, MCP tool invocation),
  a response that sets cookies, or an oversized body is recorded as completed *without* its body;
  a retry is `409 IDEMPOTENT_REPLAY_UNAVAILABLE`.
- **Routes that ignore the key:** public routes (no principal to scope it to), plus
  `/auth/logout` and `/auth/mfa/verify` (session state).
- **Store outage.** With a key present and the store unavailable the request is refused
  (`503 IDEMPOTENCY_UNAVAILABLE`), never silently executed without the guarantee.
- **Durability.** This is a retry guard, not durable exactly-once. Operations whose duplicates
  are costly keep a durable dedupe in their owning service as well (e.g. run creation, Section 9.1).

| Area | Method & path | Auth/permission | Notes |
|---|---|---|---|
| Auth | `GET /auth/login` **(public)** | — | redirects to IdP |
| Auth | `GET /auth/callback` **(public)** | — | OIDC callback |
| Auth | `POST /auth/logout` | session | revokes session + IdP token |
| Auth | `GET /auth/session` | session | current principal, roles, tenant |
| Auth | `POST /auth/mfa/enroll` | session (first enrollment); step-up once a factor exists (Section 6.6) | starts TOTP/WebAuthn enrollment |
| Auth | `POST /auth/mfa/verify` | session | completes MFA: a TOTP code, a WebAuthn registration, or a WebAuthn assertion |
| Auth | `POST /auth/mfa/challenge` | session | WebAuthn assertion options for the caller's keys (Phase A10); single-use, bound to the session |
| Auth | `GET /me/mfa` | session | the caller's factors (method, label, dates); never secret material |
| Auth | `DELETE /me/mfa/{id}` | session (owner), step-up | remove one of the caller's own factors; audited |
| Users | `GET /admin/users` | `user:manage` | tenant-scoped list |
| Invitations | `POST /invitations/{token}/accept` **(public, token-gated)** | signed invitation token + IdP login | creates/activates the invited user; IdP email MUST match the invited email (Section 6.7) |
| Sessions | `GET /me/sessions` | session | list the caller's own active sessions (Section 6.9) |
| Sessions | `DELETE /me/sessions/{id}` | session (owner only) | revoke one of the caller's own sessions |
| Users | `POST /admin/invitations` | `user:manage`, step-up | invite by email+role |
| Users | `PATCH /admin/users/{id}/roles` | `role:manage`, step-up | grant/revoke role |
| Users | `POST /admin/users/{id}/sessions/revoke` | `user:manage`, step-up | force logout |
| Users | `DELETE /admin/users/{id}` | `user:manage`, step-up | cannot delete last `org_admin` |
| Users | `POST /admin/users/{id}/mfa/reset` | `user:manage`, step-up | revokes all of the user's factors and sessions; audited (Section 7.3) |
| Roles | `GET /admin/roles` | `role:manage` | the fixed Section 2 tenant roles and their Section 7.1 permissions |
| Policies | `GET /admin/policies` | `policy:manage` | the tenant's policies (Phase A10) |
| Policies | `PATCH /admin/policies` | `policy:manage`, step-up | change tenant policies; audited |
| API Keys | `POST /me/api-keys` | session, step-up | returns secret once (Section 7.3: creating or rotating a key) |
| API Keys | `DELETE /me/api-keys/{id}` | session (owner) or `user:manage` | revoke |
| Chat | `POST /conversations` | `chat:use` | creates conversation |
| Chat | `POST /conversations/{id}/messages` | `chat:use` + resource-tenant check | returns `{run_id}` |
| Chat | `GET /runs/{id}/events` (SSE) | `chat:use` + resource-tenant check | Section 11 |
| Chat | `POST /runs/{id}/cancel` | `chat:use` + resource-tenant check | best-effort cancel |
| Artifacts | `GET /artifacts/{id}` | `artifact:read` + resource-tenant check | Section 16 |
| Artifacts | `GET /artifacts/{id}/data` | `artifact:read` + resource-tenant check | the artifact's stored result rows, read through query-gateway's result handle; `410 ARTIFACT_RESULT_EXPIRED` after the handle's TTL |
| Dashboards | `GET /dashboards` | `dashboard:read` | the caller's own dashboards plus the tenant's `tenant`-visibility ones, paginated |
| Dashboards | `POST /dashboards` | `dashboard:pin` | creates a `private` or `tenant` dashboard owned by the caller |
| Dashboards | `GET /dashboards/{id}` | `dashboard:read` + resource-tenant check | dashboard with its tiles; a `private` dashboard of another user is `404` |
| Dashboards | `POST /dashboards/{id}/tiles` | `dashboard:pin` + resource-tenant check | body `{artifact_id}`; dashboard owner only |
| Dashboards | `PATCH /tiles/{id}` | `dashboard:pin` + resource-tenant check | layout/overrides; dashboard owner only; overrides limited to Section 17's `options` keys |
| Dashboards | `POST /dashboards/{id}/share-links` | `dashboard:share`, step-up | dashboard owner only; time-boxed token, returned once |
| Dashboards | `GET /dashboards/{id}/share-links` | `dashboard:read` + resource-tenant check | the owner, or any `org_admin`; never the token |
| Dashboards | `DELETE /dashboards/{id}/share-links/{link_id}` | `dashboard:read` + resource-tenant check | the owner, or any `org_admin`; revoke now. Stopping a share never needs more than starting one did |
| Data sources | `GET/POST /data-sources` | `data:manage` | create = pending until secret set |
| Data sources | `POST /data-sources/{id}/secret` | `data:manage`, step-up | writes to Vault via metadata-service→secrets |
| Data sources | `POST /data-sources/{id}/test` | `data:manage` | sanitized connectivity result only |
| Data sources | `POST /data-sources/{id}/sync` | `data:manage` | enqueues catalog sync job |
| Data sources | `GET /data-sources/{id}` | `catalog:read` + resource-tenant check | status, `last_sync_at`; never credentials |
| Data sources | `GET/POST /data-sources/{id}/sql-grants` | `org_admin` + resource-tenant check | per-connection `sql:execute` grants to users (Section 7.1) |
| Data sources | `DELETE /data-sources/{id}/sql-grants/{grant_id}` | `org_admin` + resource-tenant check | revoke; audited |
| Catalog | `GET /data-sources/{id}/tables` | `catalog:read` + resource-tenant check | tables of a data source, paginated |
| Catalog | `GET /data-sources/{id}/tables/{table_id}` | `catalog:read` + resource-tenant check | columns and relationships |
| SQL | `POST /sql/validate` | `sql:execute` | dry validation, no execution |
| SQL | `POST /sql/execute` | `sql:execute` + per-connection grant | proxies to query-gateway; step-up above the export row threshold |
| SQL | `GET /sql/history` | `sql:execute` (own) or `run:debug` | — |
| MCP | `GET/POST /mcp/servers` | `mcp:manage` | registration = pending_approval, with a declared tool manifest; no network contact |
| MCP | `GET /mcp/servers/{id}` | `mcp:manage` + resource-tenant check | declared tools and grants; never the auth token |
| MCP | `POST /mcp/servers/{id}/approve` | `org_admin`, step-up | `pending_approval` or `disabled` -> `approved`; discovers tools and verifies the manifest against the live server |
| MCP | `POST /mcp/servers/{id}/disable` | `org_admin` | `approved` -> `disabled`, effective immediately; audited |
| MCP | `POST /mcp/servers/{id}/reject` | `org_admin` | `pending_approval` -> `rejected` (final); audited |
| MCP | `POST /mcp/servers/{id}/tools/{tool}/grants` | `org_admin`; step-up for `write`/`admin` tools | grant a role or a user |
| MCP | `DELETE /mcp/servers/{id}/tools/{tool}/grants/{grant_id}` | `org_admin` | revoke; audited |
| MCP | `POST /mcp/servers/{id}/tools/{tool}/invoke` | tool grant required; step-up for `write`/`admin` tools | proxied, policy-checked; output returned as untrusted, never stored |
| Semantic | `GET/POST /semantic/metrics` | `semantic:manage` | create = `draft`; list filters by `status`, paginated |
| Semantic | `GET /semantic/metrics/{id}` | `semantic:manage` + resource-tenant check | — |
| Semantic | `POST /semantic/metrics/{id}/approve` | `semantic:manage` + resource-tenant check | `draft` -> `approved`; audited |
| Semantic | `POST /semantic/metrics/{id}/deprecate` | `semantic:manage` + resource-tenant check | `approved` -> `deprecated`; audited |
| Semantic | `GET/POST /semantic/dimensions` | `semantic:manage` | a named, catalogued non-PII column |
| Billing | `GET /billing/usage` | `billing:read` | `?start=&end=` (UTC dates, default this month, max 366 days): LLM input/output tokens (and by stage), query minutes, current seats (Phase A11) |
| Billing | `GET /billing/quotas` | `billing:read` | today's LLM token budget: limit, used, remaining, reset time (Phase A10) |
| Billing | `POST /billing/subscription` | `billing:manage`, step-up | — |
| Audit | `GET /admin/audit` | `audit:read` | filter by actor/date/event_type |
| Notifications | `GET /me/notifications` | session | the caller's in-app notifications, newest first; `?unread_only=`, cursor-paginated |
| Notifications | `POST /me/notifications/{id}/read` | session + own notification | marks read; someone else's id is `404` (Phase A11) |
| Webhooks | `POST /admin/webhooks` | `org_admin`, step-up | `{url, event_types}`; event types from the webhook allow-list, URL under Section 15; signing secret shown once |
| Webhooks | `GET /admin/webhooks` | `org_admin` | never returns the secret (Phase A11) |
| Webhooks | `DELETE /admin/webhooks/{id}` | `org_admin`, step-up | disables the subscription and deletes its secret (Phase A11) |
| Guest share | `GET /share/{token}` **(public, token-gated)** | share-link token | read-only dashboard snapshot: names, chart specs and chart data only |

### 9.1 Representative request/response schemas

```jsonc
// POST /conversations/{id}/messages
// Request (optional `Idempotency-Key` header; replays return the same run)
{ "content": "Create a sales dashboard for Q2 with monthly revenue and regional performance.",
  "data_source_id": "ds_01..." }  // optional; required only when the tenant has more than one active data source
// Response 202
{ "run_id": "run_01HXYZ...", "conversation_id": "conv_01..." }
```

```jsonc
// GET /artifacts/{id}
{
  "artifact_id": "art_01...",
  "title": "Monthly Revenue — Q2",
  "summary": "Revenue by month for Q2, grouped by region.",
  "chart_spec": { "...": "see Section 17" },
  "result_schema": [{ "field": "month", "type": "temporal" }, { "field": "revenue", "type": "quantitative" }],
  "source_refs": [{ "data_source_id": "ds_01...", "tables": ["sales.orders"] }],
  "refresh_policy": { "mode": "manual" },
  "can_pin": true,
  "created_at": "2026-06-01T12:00:00Z"
}
```

```jsonc
// Error envelope (every 4xx/5xx)
{
  "error": {
    "code": "QUERY_VALIDATION_FAILED",
    "message": "The query uses a blocked operation.",
    "request_id": "req_01...",
    "details": { "operation": "DELETE" }
  }
}
```

---

## 10. Analytics chat is a state machine, not a free-form agent swarm

CrewAI lives inside analytics-orchestrator. A **Flow** owns the durable sequence; a small set of
specialized agents handle language reasoning. Deterministic code performs security, parsing,
validation, and execution — this is what separates an operable production system from a demo.

```
AnalyticsFlow
START
 -> load_context            (deterministic: load tenant, conversation, permissions)
 -> classify_intent         (agent)
 -> retrieve_schema         (deterministic + retrieval, Section 12)
 -> resolve_semantics       (agent + semantic-service)
 -> build_query_plan        (agent)
 -> generate_sql            (agent)
 -> validate_sql            (deterministic, Section 13)
 -> authorize_query         (deterministic)
 -> execute_query           (query-gateway call)
 -> analyze_result          (agent, bounded)
 -> build_chart_spec        (agent)
 -> validate_chart_spec     (deterministic, Section 17)
 -> persist_artifact
 -> publish_events
ERROR / RETRY / REPAIR loops are bounded (max 2 repair attempts per stage) and observable.
```

### 10.1 Agent responsibilities

| Component | LLM? | Responsibility |
|---|---|---|
| Intent agent | Yes | Convert free text into a typed `AnalyticsRequest` |
| Schema retrieval | No (retrieval model optional) | Candidate tables/columns from tenant-visible catalog |
| Semantic agent | Yes | Map business terms to approved metrics/dimensions/entities |
| Query-plan agent | Yes | Produce a structured query plan before SQL |
| SQL generator | Yes | Produce dialect-specific SQL from plan + schema + examples |
| SQL validator | No | Parse, policy-check, reject unsafe constructs, enforce limits |
| Query gateway | No | Execute using an approved read-only connection |
| Chart agent | Yes | Choose chart grammar/encodings within the bounded ChartSpec schema |
| Chart validator | No | Check schema compatibility and allowed rendering instructions |

### 10.2 Flow persistence and resumability

`analytics.runs.flow_state` persists CrewAI Flow state as JSON after every stage transition, not
only at completion. On worker crash/restart, the Flow resumes from the last completed stage
rather than restarting the whole run — required by Definition-of-Done item "Analytics runs
survive worker retries/restarts without losing state" (Section 34).

### 10.3 Guardrails specific to LLM stages (closes prompt-injection loopholes)

- **Retrieved schema/data is untrusted input.** Table/column descriptions, sample values, and
  any MCP tool output are treated as data, never as instructions. Prompts explicitly delimit
  "system instructions" from "retrieved context" and the agent is instructed to ignore any
  instruction-like text found inside retrieved context.
- **Bounded output schemas everywhere.** Every agent stage returns a typed Pydantic model
  (`AnalyticsRequest`, `QueryPlan`, `ChartSpec`, ...), not free text passed to the next stage.
  A stage whose output fails schema validation triggers a bounded repair loop, not silent
  pass-through.
- **Token/step budgets per run.** Each run has a hard wall-clock timeout (default 90s per stage,
  300s total) and a token budget (Section 23) enforced by the Flow, not by hoping the model
  stops. Exceeding the budget fails the run with `RUN_BUDGET_EXCEEDED`, not a partial silent
  result.
- **No agent ever receives raw credentials, secret_ref values, or another tenant's data.** The
  context packet built in Section 12 is pre-filtered by permission before it reaches any prompt.
- **The SQL generator never executes SQL.** Only `validate_sql` → `authorize_query` →
  `execute_query` (query-gateway, Section 13) can run anything against a real database.

---

## 11. Safe "thoughts" UI — execution trace, not chain-of-thought

Users see an execution trace: *Understanding request → Finding relevant data → Building query →
Validating → Running query → Creating visualization*. Never expose raw chain-of-thought or
private model reasoning.

```ts
type AnalyticsRunEvent = {
  runId: string;
  seq: number;
  stage: "intent" | "schema" | "semantic" | "sql" | "validation"
       | "execution" | "visualization" | "artifact" | "run";
  status: "started" | "completed" | "failed";
  message: string;      // user-safe summary only
  artifactId?: string;
  createdAt: string;
};
```

On the SSE stream each event is sent with `id: <seq>` and `event: <stage>.<status>` (e.g.
`execution.completed`, `run.completed`), so a reconnecting client resumes with `Last-Event-ID`.
A run ends with exactly one `run.completed` or `run.failed` event.

Use **Server-Sent Events (SSE)** for the browser stream (`GET /runs/{id}/events`) — the need is
one-way server→client progress. FastAPI supports SSE via `StreamingResponse`; back it with a
Redis pub/sub channel keyed by `run_id` so any api-gateway pod can serve the stream regardless of
which worker pod is processing the run. Move to WebSockets only if a genuine bidirectional
requirement appears later (e.g., interactive agent steering).

---

## 12. Schema and semantic retrieval architecture

Never dump an entire schema into every prompt. Build a metadata pipeline that extracts and
indexes database/catalog/schema/table/column metadata, relationships, usage stats, descriptions,
and sampled values (Section 8.2), then retrieve a small, permission-filtered context packet.

```
User request
  |
  +--> lexical search (names, acronyms)
  +--> vector search (semantic similarity over table/column descriptions)
  +--> relationship expansion (FK/PK/lineage, metadata.relationships)
  +--> semantic catalog (approved metrics/entities, Section 8.3)
  +--> permission filter (tenant + role + per-connection grant + column-level PII policy)
  |
  v
Context packet for agent (typed, size-bounded, no secrets, no cross-tenant leakage)
```

The semantic layer defines business concepts independently of physical tables — "Revenue" points
to an approved metric definition with grain, filters, joins, and formatting. This reduces SQL
hallucination and makes business meaning reusable across charts and dashboards.

**Column-level PII policy:** `metadata.columns.is_pii = true` columns are excluded from the
default agent context packet unless the requesting user holds an explicit `pii:read` grant for
that data source; even then, PII values are masked in chat-visible previews by default
(configurable per tenant).

---

## 13. Query Gateway — hard security boundary

The only service permitted to execute arbitrary or business SQL using customer database
credentials. metadata-service holds a narrow, explicit exception: it may use the same stored
credential only for (a) a bounded connectivity check, and (b) read-only introspection against
the database's own catalog views — never for arbitrary SQL, and never for a query whose text
originates from a user, developer, or agent. Supports
database-specific adapters, connection pooling, read-only principals, statement timeouts, max
result sizes, concurrency limits, cost estimation where available, and full audit.

```
POST /internal/v1/queries          (called only by analytics-orchestrator's execute_query step,
                                     api-gateway's /sql/execute proxy — never directly by a browser)
{
  "database_id": "db_123",
  "sql": "SELECT ...",
  "purpose": "analytics_run",
  "max_rows": 10000,
  "timeout_ms": 30000
}

-> authenticate caller (service JWT, scope=query-gateway:execute)
-> load connection policy (metadata-service, cached)
-> authorize database access (tenant + per-connection grant)
-> parse SQL with sqlglot -> AST allow-list check
-> reject multi-statement / DDL / DML(write) / forbidden functions (e.g. pg_read_file, COPY,
   dblink, xp_cmdshell-equivalents) / comments used to smuggle statements
-> apply tenant/source policy (row/column restrictions, mandatory WHERE tenant filters if the
   customer schema is itself multi-tenant)
-> optionally EXPLAIN / bounded dry run to estimate cost before executing
-> execute on a read-only DB principal (enforced at the database grant level too — defense in
   depth, never rely on the parser alone)
-> cap rows/bytes; truncate + flag if exceeded, never silently drop rows without a flag
-> store result handle in object storage with TTL (default 24h) + row-level audit record
-> emit query.completed event
```

The validator parses and regenerates SQL in the data source's dialect (Postgres, MySQL from
Phase A8); only the regenerated text executes, so comments -- including MySQL's executable
`/*! */` comments -- never reach a database.

`POST /internal/v1/queries/validate` runs the same authorization, policy load and validation
without executing (audited as `validated` or `rejected`); the Flow's `validate_sql` stage calls it,
so there is exactly one validator. A queued analytics run has no live user session:
analytics-orchestrator (and only it, only for purpose `analytics_run`) may send
`"on_behalf_of": {"tenant_id": ..., "user_id": ...}` with the `run_id`, and query-gateway
re-resolves that user's *current* principal from identity-service -- roles and status are never
taken from the caller.

`POST /internal/v1/results/read` (dashboard-service only, scope `query-gateway:results`) returns
the stored rows behind a result handle for `GET /artifacts/{id}/data`. query-gateway parses its
own handle format, serves only `analytics_run` results of the caller-named tenant, and answers
`410 RESULT_EXPIRED` once the handle's TTL has passed. It never re-executes SQL.

**SQL allow-list (v1):** `SELECT` statements only, single statement, no semicolon-chained
statements, no CTEs that call volatile/administrative functions, no file/network functions, no
`INTO`/materialization clauses, identifier allow-list must resolve against
`metadata.tables`/`columns` the caller is permitted to see (prevents blind schema probing beyond
what the catalog already exposes). Reject on parse failure — never "best effort" execute
unparseable SQL.

A generic Python sandbox is **not** the default execution path. It may post-process an
already-returned, already-capped result set (e.g., light aggregation for the chart agent), but
raw database access always goes through the steps above.

### 13.1 Database connection architecture

`+ Add Database` creates a logical `Connection` record (Section 8.2). Credentials live only in
the secrets manager.

```
Connection
  id, tenant_id, name, engine, secret_ref (Vault path, never plaintext), allowed_schemas,
  capabilities, status, created_by, created_at

Secret Manager
  secret_ref -> credentials (fetched server-side only, short cache TTL, never logged)
```

The full connection string is **never** sent to the browser after creation. Testing a connection:
browser → backend → backend fetches the secret → performs a bounded connectivity check → returns
only status + sanitized diagnostics (e.g., "Connected. 42 tables discovered." not the DSN or raw
driver error text, which can leak host/port/user).

**Connector security verification (standing rule, every engine).** A connector's defenses are
proven against a live instance of that engine in CI, never taken from driver or vendor
documentation. Drivers change protocol defaults silently (Phase A8: aiomysql requests
multi-statement support on every connection, whatever its docs imply). For each engine, integration
tests must show, on a real server:
- a write is refused even when the credential could write;
- a second statement is refused;
- the execution timeout fires;
- no file or network read works;
- the session settings the validator's parsing depends on hold against a hostile server
  configuration;
- driver messages never leak.

Connectors also check the server's identity at connect time. A protocol-compatible server of
another product (e.g. MariaDB behind the MySQL connector) is refused with `UNSUPPORTED_SERVER`,
not run with only part of the defenses applied. An engine without such an instance in CI is not
supported (see "Post-GA backlog").

---

## 14. MCP Gateway — governed tool integrations

`+ Add MCP` registers an MCP server as a governed integration (Section 8.7). The gateway
discovers tools, stores server metadata, and applies tenant/user/tool permissions before every
invocation. Treat **all** MCP tool output as untrusted external data — never re-inject it into a
prompt as if it were trusted instruction, and never assume a server is safe just because it was
approved once; classify each capability independently.

| Tool class | Default policy |
|---|---|
| Read-only metadata/search | Allow when source is approved |
| Query/data retrieval | Require explicit datasource authorization |
| External HTTP/API read | Allowlist destinations; SSRF controls (Section 15) mandatory |
| Write/mutation | Deny by default; require explicit permission + step-up confirmation |
| Admin/system operation | Admin-only, heavily audited |

Every invocation goes through `mcp.invocations` (Section 8.7) with request payload, response
status, and duration — including denied attempts, which are a security signal worth alerting on.

**Classification and policy (v1).**
- **Declared manifest.** A server is registered with a *declared* tool manifest: each tool the
  tenant intends to use, with its class. The platform makes no network contact before approval,
  because an unapproved endpoint is not yet an allowed destination (Section 15).
- **Verified at approval.** Approval (an `org_admin` with step-up) discovers the server's tools
  over MCP and verifies the manifest:
  - every declared tool must exist;
  - the server's own annotations may only make a class stricter: a tool marked
    `readOnlyHint: false` cannot be declared read-class;
  - undeclared tools are never reachable.
- **Grants decide who may invoke.** Invoking always requires a grant to the caller's role or to
  the caller, on top of an approved server and a non-`deny` tool (Section 9: "tool grant
  required"). Read classes default to `require_grant`; `write`/`admin` default to `deny`. `allow`
  stays in the schema but is not assigned in v1: Section 9's stricter rule wins over the "allow"
  row above.
- **Unknown tools.** A call naming an undeclared tool has no `tool_id` to record in
  `mcp.invocations`, so it is recorded in the audit log.
- **Output is untrusted.** It goes back to the caller marked as untrusted. Only a summary (status,
  sizes) is stored.
- **`external_read`.** The platform controls its own egress to the approved endpoint, not the
  server's outbound fetches. `external_read` tools therefore need a grant like `read_data`.

---

## 15. SSRF and outbound-network control (closes a major loophole class)

Any feature that lets the platform fetch a URL on the user's behalf — MCP server registration,
MCP tool "external_read" calls, webhook delivery, data-source connectivity tests, future
"import from URL" features — is an SSRF vector. Mandatory controls:

- Resolve DNS and re-validate the resolved IP is not link-local, loopback, multicast, or in a
  private RFC1918/RFC4193 range **at request time** (not just at registration time — DNS
  rebinding defense), unless the destination is an explicitly allow-listed internal service.
- Maintain a per-tenant allow-list for MCP server endpoints and webhook URLs; block by default,
  admin approval required to add a destination outside the allow-list (Section 14 table).
- Outbound requests from mcp-gateway and notification-service (webhooks) go through a dedicated
  egress proxy with its own network policy — these services should not share an unrestricted
  network path with, e.g., query-gateway's database egress.
- Enforce request timeouts, response size caps, and content-type allow-lists on any fetched
  external content before it is shown to a user or fed to an agent.
- Disable HTTP redirect-following by default for these fetches, or re-validate the redirect
  target against the same rules before following it.

The application-level controls ship with the feature that fetches (for MCP, Phase A9):
- resolution at request time;
- a connection pinned to the checked address, with TLS still verified against the hostname;
- HTTPS only, except explicitly allow-listed internal hosts;
- no redirects;
- time, size and content-type limits.

For webhooks (Phase A11) the same controls apply at delivery. The per-tenant allow-list is the
set of subscriptions an `org_admin` created with a fresh step-up, which is the admin approval.
The URL checks are shared with mcp-gateway through `platform-egress`.

The dedicated egress proxy and its NetworkPolicy are deployment infrastructure (Section 28),
required by Phase C1.

---

## 16. Analytics Artifact — core domain object

The chat result is a first-class artifact joining the chat interaction, generated SQL, result
schema, visualization, and dashboard-pinning lifecycle (DDL in Section 8.6).

```
AnalyticsArtifact
├── artifact_id, tenant_id, conversation_id, run_id
├── title, semantic_query, source_refs[]
├── validated_sql, query_result_ref, result_schema
├── chart_spec, refresh_policy
├── created_by, version
        |
        v  Pin to Dashboard
DashboardTile
├── tile_id, dashboard_id, artifact_id, chart_spec_version, position, overrides
```

This makes "chat → chart → pin" durable. A later instruction like "make this a stacked bar chart"
or "filter to Kerala" creates a new artifact **version**, preserving history rather than mutating
in place — required for audit and for "what changed" UX.

---

## 17. Visualization strategy — ChartSpec is the only channel to the renderer

No agent ever emits arbitrary JavaScript or HTML. A typed `ChartSpec` contract, backed by a
bounded visualization vocabulary, is rendered by a trusted library (Apache ECharts). The model
chooses a grammar; visualization-service verifies it against a JSON Schema before the frontend
ever sees it. The frontend receives JSON, never executable code.

```json
{
  "type": "line",
  "dataset": "artifact-result",
  "encoding": {
    "x": { "field": "month", "type": "temporal" },
    "y": { "field": "revenue", "type": "quantitative" },
    "color": null
  },
  "options": { "title": "Monthly Revenue", "legend": true }
}
```

**ChartSpec JSON Schema constraints (validated server-side in visualization-service):**

- `type` ∈ `{line, bar, area, scatter, pie, table}` — closed enum, no dynamic/custom types.
- `dataset` MUST reference the artifact's own `result_schema` fields only — no arbitrary field
  injection, no cross-artifact dataset references.
- `encoding.*.field` MUST exist in `result_schema`; unknown fields are rejected, not ignored.
- `options` is a narrow allow-listed key set (title, legend, stacking, colorScheme, axis labels)
  — no raw HTML/CSS/script injection points anywhere in the schema.
- Reject on any field the schema doesn't recognize (strict/`additionalProperties: false`), rather
  than silently dropping it — silent-drop hides prompt-injection attempts that tried to smuggle
  extra instructions through the chart payload.

---

## 18. Async execution and eventing

A full analytics run is too important to tie to one HTTP request/response. The API creates an
`AnalyticsRun`, enqueues work, and streams status via SSE (Section 11).

```
Browser -> POST message -> api-gateway -> analytics-orchestrator
                                              -> create run
                                              -> publish analytics.run.requested
                                                     |
                                                     v
                                              Worker / Flow
                                    +------------+------------+-------------+
                                    |            |            |
                             metadata events  query events  chart events
                                    +------------+------------+-------------+
                                                     |
                                                     v
                                          Analytics Run state (Postgres)
                                                     |
                                                     v
                                              SSE stream -> Browser
```

Redis supports ephemeral state, cache, and pub/sub (SSE fan-out) — it is **not** the durable
system of record for long-running analytics; Postgres (`analytics.runs`, `analytics.run_events`)
is.

### 18.1 Topic contracts (NATS subject / Kafka topic naming: `<domain>.<entity>.<event>`)

| Topic | Producer | Consumers | Payload (JSON Schema in `contracts/events/`) |
|---|---|---|---|
| `analytics.run.requested` | analytics-orchestrator | worker-runtime | `{run_id, tenant_id, conversation_id}` |
| `analytics.run.stage_changed` | analytics-orchestrator (Flow) | api-gateway (SSE bridge) | `AnalyticsRunEvent` (Section 11); carried on Redis pub/sub channel `analytics:run:{run_id}`, durable copy in `analytics.run_events` |
| `query.completed` | query-gateway | analytics-orchestrator, notification-service | `{query_id, run_id, status, row_count}` — not produced yet: queries are synchronous, so nothing waits on one. Producer and notification arrive with async query/export execution (post-GA backlog) |
| `metadata.sync.requested` | metadata-service, scheduler | worker-runtime | `{data_source_id, tenant_id}` |
| `metadata.sync.completed` | worker-runtime (metadata-service while sync runs in-request, ADR 0004) | metadata-service, notification-service | `{data_source_id, data_source_name, status, tables_synced, user_id}` (`user_id` = who ran it, the notification recipient) |
| `dashboard.tile.pinned` | dashboard-service | notification-service | `{dashboard_id, artifact_id, user_id}` |
| `mcp.invocation.denied` | mcp-gateway | notification-service, audit | `{tool_id, tenant_id, reason}` (security signal) |
| `identity.role.changed` | identity-service | audit, notification-service | `{user_id, roles, granted, revoked, changed_by}` (one event per change; `roles` is the result) |
| `billing.usage.recorded` | multiple (query-gateway, analytics-orchestrator) | worker-runtime (aggregation) | `{event_id, tenant_id, metric, quantity, occurred_at}` (+ `model`, `stage`, `run_id` for LLM usage) |

Every event carries `tenant_id`, `request_id`/`run_id` for trace correlation, and a schema
version. Consumers reject unknown major schema versions rather than guessing field meaning.

---

## 19. Multi-tenancy and data isolation

- `tenant_id` on every tenant-owned table (Section 8); indexed first in every composite index
  used for list/filter queries.
- One shared PostgreSQL cluster with **per-service schemas** is an acceptable MVP deployment;
  physical database-per-service (or per-large-tenant) is a scale-out option, not a rewrite,
  because the service boundary (not the schema boundary) is what the application code depends on.
- Postgres Row-Level Security policies (`USING (tenant_id = current_setting('app.tenant_id')::uuid)`)
  are enabled as defense-in-depth on every tenant-owned table; the application sets
  `app.tenant_id` at the start of each request-scoped DB session. This catches the case where an
  application-layer tenant filter is accidentally missing from a query.
- Never share one Postgres role/credential with `BYPASSRLS` for application traffic — only
  migration/admin tooling uses an RLS-bypass role, and that role is never reachable from request
  handling code paths.
- Large/enterprise tenants MAY be isolated onto dedicated infrastructure (separate DB instance,
  separate worker pool, separate data region) — `identity.tenants.data_region` exists for this
  from day one so it isn't a later migration.

### 19.1 Entity ownership — see Section 8.9.

---

## 20. Performance and optimization rules

- Connection pools sized per service and per external database capacity; never let a request
  create a fresh DB engine/connection.
- Cache schema metadata and semantic retrieval results with explicit invalidation/TTL — never
  serve stale schema snapshots indefinitely (surface `last_sync_at` in the UI).
- Paginate all list endpoints; stream large exports rather than buffering fully in memory.
- Cap rows/bytes returned by analytics queries; prefer aggregation in the source database over
  moving raw tables through Python.
- Keep agent prompts small via retrieval, not truncation — store reusable semantic context and
  query examples in a catalog (Section 12).
- Background workers handle metadata sync, profiling, exports, scheduled refresh, and expensive
  analysis — never block an HTTP request on these.
- Use async I/O for network-heavy services; don't force CPU-bound work into async functions
  (offload to a worker/process pool instead).
- Idempotency keys (Section 9) on create/run commands a browser might retry.
- Set explicit **query gateway concurrency limits per tenant** so one noisy tenant cannot starve
  another tenant's queries on shared infrastructure (a fairness/DoS-prevention control, not just
  a performance one).

---

## 21. Error handling standard

```json
{
  "error": {
    "code": "QUERY_VALIDATION_FAILED",
    "message": "The query uses a blocked operation.",
    "request_id": "req_01...",
    "details": { "operation": "DELETE" }
  }
}
```

Never return raw stack traces, database credentials, provider secrets, SQL-parser internals, or
model prompts to clients. Log the full diagnostic server-side keyed by `request_id`/`run_id`.
Map infrastructure failures to a stable, documented set of API error codes (`contracts/openapi/`
enumerates them per service so the frontend can branch on `error.code`, not on `error.message`
text, which may change).

---

## 22. Observability and audit

Every cross-service action is traceable. Create a `request_id` per inbound request and a
`run_id` per analytics job; propagate W3C trace context across HTTP and messaging.
OpenTelemetry instruments traces/metrics/logs; ship to a collector, backed by
Prometheus/Grafana + Tempo/Jaeger + Loki (or the organization's existing stack).

| Identifier | Purpose |
|---|---|
| `request_id` | Trace one HTTP request |
| `conversation_id` | Group a chat conversation |
| `run_id` | Trace one analytics attempt end-to-end |
| `query_id` | Trace one database execution |
| `artifact_id` | Trace one analytic result/chart |
| `dashboard_id` | Trace a pinned presentation |

Audit high-value events (append-only, Section 8.1): login/logout, MFA changes, role changes,
permission changes, connection creation, connection secret changes, SQL execution, MCP
invocation (including denials), exports, dashboard visibility/sharing changes, artifact
deletion, impersonation start/end, and all admin actions.

### 22.1 SLOs and alerting (minimum set for GA)

| SLO | Target | Alert condition |
|---|---|---|
| API gateway availability | 99.9%/month | error rate > 1% over 5 min |
| p95 chat "first event" latency | < 3s from message POST to first SSE event | p95 > 5s over 10 min |
| p95 analytics run completion | < 30s for single-table queries | p95 > 60s over 15 min |
| Query gateway execution success rate | > 98% (excluding user SQL errors) | < 95% over 15 min |
| Auth failure rate | baseline + anomaly detection | spike > 5x rolling baseline (credential-stuffing signal) |
| MCP invocation denial rate | informational | spike > 5x rolling baseline (abuse signal) |

---

## 23. Cost governance and LLM provider abstraction

- All LLM calls go through a single internal `ModelRouter` module in analytics-orchestrator —
  agents never call a provider SDK directly. This allows provider/model swapping and enforces
  budgets centrally.
- Per-tenant **token budgets** (daily/monthly) and per-run token/step caps (Section 10.3) are
  enforced by the router before a call is made, not audited only after the fact.
- Track `billing.usage.recorded` events per tenant for: LLM tokens (by stage), query-gateway
  execution seconds, storage bytes, seats. Aggregate in worker-runtime for billing/usage
  dashboards (Section 9's `/billing/usage`).
  - Phase A11: worker-runtime consumes the events and writes them through
    analytics-orchestrator, which owns `analytics.usage_records` and serves `/billing/usage`.
  - Seats are counted live from identity-service (active users), not metered as events.
  - Storage bytes wait for artifact storage metering (post-GA backlog).
- Support at least one fallback model/provider per stage for availability (e.g., primary +
  fallback), selected by the router on error/timeout, never mid-flow silently for cost reasons
  without a policy flag.
- Cache deterministic sub-results where safe (e.g., schema retrieval, semantic resolution for an
  identical request) to reduce redundant token spend — never cache raw query results across
  tenants, and never cache anything containing PII beyond its stated TTL.

---

## 24. Security loophole checklist (expanded, OWASP-aligned + AI-specific)

This section is the authoritative "what not to build" list. Each item maps to a control already
specified elsewhere in this document — treat this as the checklist a reviewer runs against a PR.

**Access control**
- [ ] No endpoint authorizes on permission alone without a resource-tenant check (Section 7.2).
- [ ] No admin/step-up action is reachable without a fresh MFA/re-auth check (Section 7.3).
- [ ] Cross-tenant resource IDs return `404`, never `403` (avoids existence-enumeration).
- [ ] Frontend route grouping/role checks are never the only gate (Section 7.4).
- [ ] The last `org_admin` of a tenant cannot be demoted or deleted (Section 2).

**Credentials and secrets**
- [ ] No customer database credential is ever sent to, or requested from, the browser.
- [ ] No secret is logged, included in an error response, or embedded in an audit `before/after`
      diff (redact secret-shaped fields before writing `audit_events`).
- [ ] No shared root/admin DB credential is used for application query execution — read-only,
      least-privilege DB principals only (Section 13, 19).
- [ ] API keys are stored hashed, never reversible (Section 6.8).

**Injection and execution**
- [ ] No unrestricted Python/JS execution path exists anywhere in the request path (chat, MCP
      tools, chart rendering). Sandboxed post-processing only operates on already-capped,
      already-returned result sets.
- [ ] All SQL passes through the AST allow-list validator; no "trusted because it came from our
      own LLM" bypass exists anywhere in the code (Section 13).
- [ ] ChartSpec has no field that accepts raw HTML/CSS/JS (Section 17).
- [ ] All MCP tool output is treated as untrusted data, never as trusted instruction (Section 10.3, 14).

**SSRF / outbound network**
- [ ] Every outbound fetch initiated on a user's behalf re-validates the destination IP at
      request time, not just at registration time (Section 15).
- [ ] Webhook and MCP endpoints are allow-listed per tenant; redirects are not blindly followed.

**Multi-tenancy**
- [ ] Every tenant-owned table has RLS enabled in addition to application-layer filtering
      (Section 19).
- [ ] No cross-service "just query the other service's database directly" shortcut exists — call
      the owning service's API/contract instead (Non-negotiable rules, Section 33).

**AI-specific**
- [ ] No stage of the Flow passes unbounded free text to the next stage — every hop is a typed,
      schema-validated model (Section 10.3).
- [ ] Retrieved schema text, sample values, and MCP output are explicitly marked as data, not
      instructions, inside every prompt template.
- [ ] Token/step/time budgets are enforced by the Flow itself, not left to "the model should stop."
- [ ] Chain-of-thought / private reasoning is never shipped to the client — only the user-safe
      `AnalyticsRunEvent` stream (Section 11).

**Rate limiting and abuse**
- [ ] Auth endpoints (login, MFA verify, password reset) have per-account and per-IP rate limits
      independent of the general API rate limiter.
- [ ] `sql:execute` and MCP invocation both have per-tenant concurrency and per-minute rate
      limits so one tenant cannot exhaust shared query-gateway capacity (Section 20).
- [ ] Export/download endpoints have row/byte thresholds that trigger step-up auth (Section 7.3).

**Supply chain / build**
- [ ] CI runs dependency vulnerability scanning (`pip-audit`/`osv-scanner`, `npm audit`) and
      container image scanning on every build; blocks on high/critical findings (Section 26).
- [ ] No long-lived cloud credentials in repo secrets — CI uses short-lived OIDC federation to
      the cloud provider.
- [ ] Container images are built from pinned base image digests, run as non-root, and are signed
      (cosign) before deployment.

**Data lifecycle**
- [ ] Query result handles (Section 8.5) expire on a TTL; nothing keeps customer query results
      indefinitely by default.
- [ ] Tenant deletion has a defined, tested cascade (Section 6.7-adjacent) across every owning
      service, not just the identity record.
- [ ] PII columns are excluded from default agent context and masked in chat previews unless an
      explicit `pii:read` grant exists (Section 12).

---

## 25. Testing strategy

| Layer | Examples |
|---|---|
| Unit | SQL policy parser, semantic resolver, ChartSpec validator, permission policy functions |
| Integration | FastAPI + Postgres (testcontainers), service + Redis/broker, connector tests against ephemeral test DBs |
| Contract | OpenAPI/event-schema compatibility between gateway and services (`contracts/` diffed in CI) |
| E2E (Playwright) | Login → chat → run → chart → pin; developer SQL flow; admin role change; guest share-link flow |
| Security | BOLA/IDOR tests (every resource endpoint with a foreign tenant's ID), tenant isolation (RLS bypass attempts), role escalation, CSRF, SSRF (Section 15 controls), unsafe SQL corpus (Section 13 allow-list), MCP tool policy denial tests |
| AI evaluation | Intent-classification accuracy, table-recall@k, SQL validity rate, execution success rate, chart-encoding correctness, groundedness (does the answer reference only retrieved/approved context), prompt-injection resistance corpus (Section 24 "AI-specific" checklist as test cases) |

Authorization tests are not optional — every endpoint that accepts a user-supplied identifier
gets a same-tenant-allowed / cross-tenant-404 / no-permission-403 test triplet at minimum.

Database connectors follow the standing verification rule in Section 13.1: their security
defaults are proven by integration tests against a live instance of each engine (and a refused
look-alike server), never from documentation alone.

---

## 26. CI/CD and code review gates

1. **Pre-commit:** Ruff format/lint, ESLint/Prettier, type checks for changed packages, unit tests
   for changed packages only (fast feedback).
2. **Pull request (full pipeline):**
   - Full unit/integration test matrix per changed service (uv workspace makes this selective).
   - OpenAPI generation + diff against `contracts/openapi/` — fail on breaking change without a
     version bump.
   - Event schema diff against `contracts/events/`.
   - Dependency vulnerability scan (`pip-audit`, `npm audit`/`osv-scanner`).
   - Container build (multi-stage, non-root) + image scan (Trivy/Grype); block on high/critical.
   - Static analysis / secret scanning (gitleaks) on the diff.
   - mypy on `core/domain/application` layers of touched Python services.
3. **Contract verification:** reject incompatible API/event schema changes unless explicitly
   versioned (new `/v2` route or new event schema major version).
4. **Deploy:** run DB migrations as a separate controlled job (Alembic upgrade, one service at a
   time, with a rollback script recorded), then deploy the service, then health checks, then
   smoke tests against a synthetic tenant.
5. **Production:** canary or rolling deployment, structured logs/traces/metrics active from pod
   start, alert rules loaded, documented rollback procedure (`docs/runbooks/rollback-<service>.md`).

**Example GitHub Actions skeleton** (`.github/workflows/ci.yml`, abbreviated):

```yaml
name: ci
on: [pull_request]
jobs:
  python-services:
    strategy:
      matrix:
        service: [api-gateway, identity-service, analytics-orchestrator, metadata-service,
                   semantic-service, query-gateway, visualization-service, dashboard-service,
                   mcp-gateway, worker-runtime, notification-service]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv python install 3.12
      - run: uv sync --package ${{ matrix.service }}
      - run: uv run --package ${{ matrix.service }} ruff check .
      - run: uv run --package ${{ matrix.service }} ruff format --check .
      - run: uv run --package ${{ matrix.service }} mypy src/${{ matrix.service }}/core src/${{ matrix.service }}/domain src/${{ matrix.service }}/application
      - run: uv run --package ${{ matrix.service }} pytest apps/${{ matrix.service }}/src/*/tests/unit
      - run: uv run --package ${{ matrix.service }} pytest apps/${{ matrix.service }}/src/*/tests/integration
  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 20 }
      - run: npm ci --prefix web/next-app
      - run: npm run lint --prefix web/next-app
      - run: npm run typecheck --prefix web/next-app
      - run: npm run test --prefix web/next-app
  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: gitleaks/gitleaks-action@v2
      - run: pip install pip-audit && pip-audit -r apps/**/pyproject.toml || true
      - run: npm audit --prefix web/next-app --audit-level=high
  contracts:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: scripts/gen-openapi.sh
      - run: scripts/diff-contracts.sh   # fails on breaking change without version bump
```

---

## 27. Local development environment (docker-compose.dev.yml)

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: devpass
      POSTGRES_DB: agentic_bi
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]

  redis:
    image: redis:7
    ports: ["6379:6379"]

  nats:
    image: nats:2-alpine
    command: ["-js"]
    ports: ["4222:4222"]

  keycloak:
    image: quay.io/keycloak/keycloak:25.0
    command: start-dev
    environment:
      KEYCLOAK_ADMIN: admin
      KEYCLOAK_ADMIN_PASSWORD: admin
    ports: ["8080:8080"]

  vault:
    image: hashicorp/vault:1.17
    cap_add: ["IPC_LOCK"]
    environment:
      VAULT_DEV_ROOT_TOKEN_ID: devroot
    ports: ["8200:8200"]

  minio:
    image: quay.io/minio/minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports: ["9000:9000", "9001:9001"]

  mailhog:
    image: mailhog/mailhog
    ports: ["8025:8025", "1025:1025"]

  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    volumes: ["./infra/compose/otel-collector.yaml:/etc/otelcol/config.yaml"]
    ports: ["4317:4317", "4318:4318"]

  # sample DB the platform will connect to as a customer data source, for the first vertical slice
  sample-sales-db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: sales
      POSTGRES_DB: sample_sales
    ports: ["5433:5432"]

volumes:
  pgdata:
```

`Makefile` targets to standardize local workflows: `make up` (compose up), `make migrate`
(run Alembic upgrade for every service against the shared Postgres, one schema each),
`make seed` (seed a demo tenant + sample-sales-db + demo users for all three roles),
`make dev` (start all FastAPI services with reload + `next dev`).

---

## 28. Deployment, infrastructure, and disaster recovery

- **Containers:** multi-stage Docker builds, non-root runtime user, health endpoints
  (`/health/live`, `/health/ready`), CPU/memory limits set, config via environment (12-factor).
- **Orchestration:** Kubernetes; one Helm chart (or Kustomize base+overlay) per service, shared
  library chart for common bits (probes, HPA, service account, network policy).
- **Networking:** ingress/API gateway terminates TLS at the edge; internal service-to-service
  traffic on mTLS via service mesh once beyond MVP (Section 6.3); Kubernetes `NetworkPolicy`
  restricts which services may reach query-gateway and mcp-gateway's egress paths.
- **IaC:** Terraform modules for VPC, managed Postgres (RDS/CloudSQL), managed Redis, managed
  Kafka/NATS (or self-hosted StatefulSet), EKS/GKE cluster, DNS, secrets manager, object storage.
  Environments: `dev`, `staging`, `prod` as separate Terraform workspaces/state, never shared
  state across environments.
- **Secrets in infra:** Vault (or cloud secrets manager) is provisioned by Terraform; application
  secrets are written by a controlled process, never committed, never templated into Helm values
  in plaintext.
- **Backup/DR:** automated Postgres point-in-time-recovery backups (daily full + WAL archiving);
  documented RPO ≤ 15 min, RTO ≤ 4 hours for MVP, tested via a quarterly restore drill
  (`docs/runbooks/dr-restore-drill.md`). Object storage (query result exports) has lifecycle
  rules matching the result-handle TTL (Section 13).
- **Environments and promotion:** `dev` (shared, ephemeral-friendly) → `staging` (production-like,
  synthetic tenant, used for E2E/security test runs and the quarterly DR drill) → `prod`. No
  direct-to-prod deploys; every prod deploy is a promoted, previously-staged build artifact.
- **Data residency:** `identity.tenants.data_region` selects the deployment region/cluster a
  tenant's data lives in from creation — do not bolt this on later once tenants exist in a single
  region.

---

## 29. Compliance and data governance

- **PII classification:** `metadata.columns.is_pii` flag (Section 8.2), enforced in retrieval
  (Section 12) and in export/audit redaction (Section 24).
- **Data subject rights:** tenant-level "export all my data" and "delete all my data" admin
  operations MUST exist before GA, implemented as a documented cascade across every owning
  service (mirrors the tenant-deletion cascade in Section 24's data-lifecycle checklist).
- **Data retention defaults:** query result handles 24h TTL (Section 13), audit events retained
  indefinitely (append-only, compliance requirement), chat/run history retained per tenant plan
  with a configurable retention window, notification records retained 90 days.
- **DPA / sub-processor list:** required before onboarding any customer whose data includes
  personal data of EU/UK/California residents; maintain a documented sub-processor list (cloud
  provider, LLM provider, email provider) — this is a legal/operational requirement, not a code
  requirement, but the architecture (Section 28 data residency, Section 24 credential handling)
  must not contradict whatever the DPA promises.
- **LLM data handling:** document, per configured model provider, whether prompts/context are
  used for provider-side training — default to providers/settings that disable training on
  customer data; this constraint belongs in the `ModelRouter` config (Section 23), not just a
  policy document.

---

## 30. Development sequence used by Section 31's build plan

Do not implement the whole platform at once. Lock down architecture and contracts, then build
one vertical slice proving the core business loop, then widen. This is the capability-level
summary; Section 31.0 splits it into an explicit **backend + CrewAI first, frontend second**
execution order (Track A / Track B / Track C) — item 6 below ("Client Chat UI") is backend-proven
over HTTP in Track A before any UI is built for it in Track B.

1. Monorepo, CI, uv workspace, linting, typing, test setup, Docker baseline, documentation.
2. Identity + tenant model, BFF/gateway authentication path, before any analytics. Role/permission
   tests before exposing any protected route.
3. Metadata Service with a PostgreSQL connection catalog and one Postgres connector; schema sync
   job; searchable catalog.
4. Query Gateway with read-only execution, SQL parser/validator, limits, timeouts, audit. Test
   security before connecting an LLM to anything.
5. Analytics Orchestrator + one CrewAI Flow for a single database/dialect. Persist `AnalyticsRun`
   state; stream safe progress events via SSE.
6. Client Chat UI rendering a single ECharts chart from a typed ChartSpec. Pin to Dashboard +
   Dashboard Service.
7. Developer SQL Editor against the same Query Gateway; generated SQL inspectable/editable there.
8. Semantic Service and metric definitions — only after semantic grounding is stable does the
   agent get wider schema access.
9. More databases via connector interfaces, not by branching core query logic.
10. MCP Gateway with read-only tools first; write-capable tools only after policy/approval/
    confirmation mechanisms exist.
11. Admin console, advanced audit, quotas, model/provider routing, operational dashboards.
12. Notification service, webhooks, billing usage.
13. Only then: Superset integration, embedded Superset, or selective API reuse.

### 30.1 Python toolchain: uv + Python 3.12 + per-service dependencies

`uv` is the Python project/dependency/build workflow; CPython 3.12 is pinned repo-wide (mature,
broadly supported, Tier-1 in `uv`). Bootstrap:

```bash
uv python install 3.12
uv init --python 3.12
uv run python --version
uv sync
```

Commit `pyproject.toml`, `.python-version`, `uv.lock`. Do not commit `.venv`.

**Each independently deployable FastAPI service owns its runtime dependencies** — do not put
every dependency for every microservice into one root environment:

```bash
uv add --package analytics-orchestrator fastapi crewai
uv add --package query-gateway fastapi sqlalchemy sqlglot
uv add --package identity-service fastapi sqlalchemy pydantic-settings authlib
uv add --package metadata-service fastapi sqlalchemy asyncpg
uv add --dev pytest pytest-asyncio ruff mypy testcontainers
```

Shared internal libraries (`packages/python/*`) are explicit workspace dependencies, never
accidental imports across service boundaries. `uv` workspaces share one lockfile while each
member keeps its own `pyproject.toml` — fits the monorepo/independently-deployable-service model
provided services don't require conflicting Python version ranges.

CrewAI belongs primarily in analytics-orchestrator. Use Flows for the deterministic business
workflow; introduce Crews only where bounded agent collaboration is genuinely useful. Do not let
an LLM framework become the application architecture.

Dependency policy: `uv add` to declare, review the resulting `pyproject.toml`, run tests, commit
`uv.lock`. No unmanaged `pip install` into project environments. Pin exact versions only for a
compatibility/security reason; otherwise use controlled ranges and let the lockfile provide
reproducibility. Update dependencies in a dedicated, tested change.

The Next.js frontend keeps its own `package.json`/lockfile — `uv` never manages Node dependencies.

---

## 31. Deterministic phase-by-phase build plan (for a human or an AI coding agent)

Each phase below is self-contained: goal, exact commands/files, and a **Definition of Done (DoD)**
that must pass before moving to the next phase. This is the section to execute top-to-bottom.

### 31.0 Development order: backend + CrewAI first, frontend second

This is a deliberate, supported build order, not just a possible one. It works cleanly here
because of two properties this architecture already has:

1. **Every service is an independently runnable, independently testable HTTP API.** FastAPI
   generates OpenAPI for each service automatically, and Section 9 already defines every request/
   response contract up front. Nothing about the backend requires a browser to exist — it can be
   fully exercised with `pytest` + `httpx`, `curl`, or Postman/Insomnia against real containers
   (`docker-compose.dev.yml`, Section 27).
2. **CrewAI lives entirely inside one backend service** (analytics-orchestrator, Section 10) and
   is driven by the same HTTP/SSE contract the frontend will eventually call
   (`POST /conversations/{id}/messages`, `GET /runs/{id}/events`). Proving the Flow works means
   proving those two endpoints work — again, no UI required.

The plan below is split into three tracks, run **in order**:

| Track | Contains | Frontend code touched? |
|---|---|---|
| **Track A — Backend + CrewAI** (A0–A12) | Every microservice, every database schema, the full CrewAI Flow, query gateway security, MCP gateway, admin/notification/billing APIs | **No.** `web/next-app` is scaffolded once in A0 and not touched again until Track B. |
| **Track B — Frontend** (B1–B7) | The entire Next.js app, consuming the OpenAPI client generated from Track A's already-tested contracts | Yes — this is the only track that writes frontend code. |
| **Track C — Hardening** (C1–C2) | Cross-cutting production readiness, optional Superset | Both, but only fixes/polish, no new features. |

**Why this order is safe (and not just faster to type):** because Sections 6–24 of this document
already fully specify auth, permissions, data model, and API contracts before any code is
written, the frontend in Track B has nothing to guess at — it implements against a fixed,
already-integration-tested contract (`contracts/openapi/*.json`, generated in Track A and
committed to the repo). This is the opposite of the usual risk with backend-first development
(the frontend team blocked on a moving API) because the API isn't moving — it was pinned in
Section 9 before Phase A0 started. Track A's exit gate (Phase A12) is the checkpoint: it does not
open Track B until the full backend vertical slice — including CrewAI — passes its own automated
test suite with no UI involved.

**Practical note on local auth testing without a browser (Phase A1):** OIDC's Authorization
Code + PKCE flow is designed for browsers, so Track A verifies login end-to-end using the
**Resource Owner Password / direct grant** or a scripted headless browser flow
(`scripts/test-login.sh`, using `httpx` to drive Keycloak's token endpoint directly against the
dev realm) — this is a *test-only* shortcut used purely to prove identity-service and api-gateway
work correctly; production auth still only ever uses Authorization Code + PKCE via the browser
(Section 6.1), which Track B wires up for real.

---

### Track A — Backend + CrewAI (no frontend code)

### Phase A0 — Monorepo bootstrap

```bash
mkdir buvi && cd buvi          # already done
git init                        # already done
uv init --name buvi --python 3.12   # already done — creates root pyproject.toml as uv workspace root
uv python install 3.12          # already done
mkdir -p apps web/next-app packages/python packages/ts infra/{docker,compose,kubernetes,terraform} \
         contracts/{openapi,events,json-schema} docs/{architecture,adr,runbooks} scripts .github/workflows
```

- Add `[tool.uv.workspace] members = ["apps/*", "packages/python/*"]` to root `pyproject.toml`.
- Create `Makefile`, root `README.md`, `.gitignore` (must ignore `.venv`, `node_modules`, `.env`).
- Create `packages/python/platform-contracts`, `platform-observability`, `platform-auth`,
  `platform-testing` as `uv init --lib` packages (Section 4).
- Create `web/next-app` with `npx create-next-app@latest --typescript --app --src-dir --tailwind`
  now (so the workspace/CI shape is complete), but **do not add pages/features to it** until
  Track B — it stays at its scaffolded default until Phase B1.
- Set up ESLint/Prettier, Ruff config (`ruff.toml` at repo root, inherited by all packages), mypy
  config, `.github/workflows/ci.yml` (Section 26, can start with lint+test only, expand later).
- **DoD:** `uv sync` succeeds at the root; `npm ci && npm run build` succeeds in `web/next-app`
  (proves the scaffold is healthy, not that any feature exists); CI pipeline runs green on an
  empty-service commit; `docs/adr/0001-architecture-baseline.md` records this document as the
  baseline ADR.

### Phase A1 — Local infra + identity-service (API-only; no browser involved)

- Add `infra/compose/docker-compose.dev.yml` (Section 27). `make up` brings up Postgres, Redis,
  NATS, Keycloak, Vault, MinIO, MailHog, OTel collector.
- Scaffold `apps/identity-service` per Section 4.1 structure via `uv init --package identity-service`.
- `uv add --package identity-service fastapi sqlalchemy[asyncio] asyncpg alembic pydantic-settings authlib argon2-cffi`.
- Implement DDL from Section 8.1 as the first Alembic migration.
- Implement OIDC Authorization Code + PKCE against local Keycloak realm (create a
  `scripts/keycloak-bootstrap.sh` that provisions a dev realm, client, and the three demo roles).
- Implement `GET /auth/login`, `GET /auth/callback`, `POST /auth/logout`, `GET /auth/session`
  (Section 6.1, 9).
- Implement `Principal`/`require_permission`/`require_resource_owner` in
  `packages/python/platform-auth` (Section 7.2) and consume it from identity-service's own
  protected routes first (self-test the pattern before other services depend on it).
- Implement MFA enrollment/verify (TOTP first; WebAuthn can follow in Phase A10).
- Implement invitations, sessions list/revoke, API keys (Sections 6.5–6.9).
- Write `scripts/test-login.sh` (or a pytest fixture) that drives Keycloak's token endpoint
  directly (test-only direct grant, per 31.0) to obtain a token for each of the three demo roles,
  exercises `GET /auth/session`, and confirms the returned `tenant_id`/permission set is correct
  — this is the backend-only stand-in for "a human logs in through the browser," deferred to
  Phase B1.
- **DoD:** the scripted flow registers/invites, verifies email (via MailHog, Section 27), logs
  in, and logs out entirely over HTTP, for all three demo roles; session cookie/token is
  HttpOnly/Secure/SameSite-correct when inspected at the HTTP layer; full authorization-test
  triplet (Section 25) passes for every identity-service endpoint; audit events are written for
  login/logout/role changes. **No Next.js code is written in this phase.**

### Phase A2 — API Gateway

- Scaffold `apps/api-gateway`. This is the only service the Next.js BFF calls.
- Implement request correlation (`request_id` generation), JWT/session validation via
  `platform-auth`, rate limiting (Redis token bucket, Section 20/24), and routing to internal
  services over service-JWT auth (Section 6.3).
- Implement the error envelope (Section 21) as a shared exception handler.
- **DoD:** every route in Section 9's catalog exists as a routed (even if some backends are
  stubbed 501 for now) endpoint with authentication enforced; rate-limit test proves 429 on
  burst; contract test exports `contracts/openapi/api-gateway.json`.

### Phase A3 — Metadata Service (one Postgres connector)

- Scaffold per Section 4.1; implement DDL from Section 8.2.
- Implement `Connection` CRUD (Section 13.1), Vault integration for `secret_ref` write/read
  (`packages/python/` shared secrets client), connectivity test endpoint returning sanitized
  diagnostics only.
- Implement schema sync job (initially synchronous for MVP, moved to worker-runtime in Phase 5):
  introspect a Postgres data source → populate `tables`/`columns`/`relationships`.
- **DoD:** an `org_admin`/`developer` can add the `sample-sales-db` compose service as a
  connection, test it, sync it, and see tables/columns in the catalog API; secret never appears
  in any API response or log; authorization-test triplet passes.

### Phase A4 — Query Gateway (security boundary before any LLM)

- Scaffold per Section 4.1; implement DDL from Section 8.5.
- Implement the SQL validation pipeline with `sqlglot` per Section 13's allow-list rules as a
  standalone, heavily unit-tested module (`domain/policies/sql_validator.py`) — write the "unsafe
  SQL corpus" test suite from Section 25 **before** wiring execution.
- Implement the Postgres connector adapter (`infrastructure/connectors/postgres.py`), read-only
  DB principal, statement timeout, row/byte caps, result handle → MinIO with TTL.
- Implement `POST /internal/v1/queries` service-to-service only (no public route yet).
- **DoD:** the unsafe-SQL corpus (DDL/DML/multi-statement/file functions/etc.) is 100% rejected;
  a valid `SELECT` against `sample-sales-db` executes, returns a capped result, and writes a
  `query_executions` audit row; no credential ever appears in a response, log, or error message.

### Phase A5 — Analytics Orchestrator + first CrewAI Flow (single DB, single dialect)

- Scaffold per Section 4.1; implement DDL from Section 8.4.
- Implement the `AnalyticsFlow` (Section 10) as a CrewAI Flow, end-to-end against **one** Postgres
  data source only: `load_context → classify_intent → retrieve_schema → build_query_plan →
  generate_sql → validate_sql → authorize_query → execute_query → build_chart_spec →
  validate_chart_spec → persist_artifact → publish_events`. Skip `resolve_semantics` for this phase
  (Section 30 step 8 adds it later) — hardcode "use tenant-visible tables directly." `analyze_result`
  is also deferred; both are wired in Phase A7.
- The Flow executes inside analytics-orchestrator (Sections 3, 10, 30.1). `retrieve_schema` reads an
  agent context packet from metadata-service (agent-visible tables, non-PII columns, never
  `secret_ref`; Sections 10.3, 12). `validate_sql` calls query-gateway's
  `POST /internal/v1/queries/validate` and `execute_query` calls `POST /internal/v1/queries` on behalf
  of the requesting user (Section 13) — the Flow never holds its own copy of the validator.
- `validate_chart_spec` checks a strict `ChartSpec` model in `packages/python/platform-contracts`
  (Section 17); Phase A6's visualization-service takes over that validation from the same model.
  `persist_artifact` stores the `AnalyticsArtifact` (Section 16) in the run's `flow_state` and
  references it by `artifact_id` in `run_events`; Phase A6 moves the canonical store to
  dashboard-service (Section 8.9).
- Implement Flow-state persistence to `analytics.runs.flow_state` after every stage (Section 10.2);
  a resumed run skips completed stages and never re-emits their events.
- Wire `worker-runtime` as the durable JetStream consumer of `analytics.run.requested` (introduce
  worker-runtime now, even though its full job catalog comes later): it drives the run's execution
  in analytics-orchestrator and redelivers it if the executor or the worker dies mid-run.
- Implement SSE bridge: the Flow persists each `AnalyticsRunEvent` and publishes it to Redis pub/sub
  channel `analytics:run:{run_id}`; api-gateway's `GET /runs/{id}/events` replays persisted events,
  then streams live ones (Section 11, 18).
- Implement the `ModelRouter` (Section 23) with one provider/model and budget enforcement from
  day one — per-run token cap, per-tenant daily token cap, 90s per stage, 300s per run — do not
  defer budget enforcement to "later."
- `POST /conversations/{id}/messages` honours `Idempotency-Key` through `analytics.runs.idempotency_key`.
- **DoD:** `POST /conversations/{id}/messages` with "Create a sales dashboard for Q2 with monthly
  revenue" produces a `run_id`; the SSE stream shows the full user-safe stage sequence (Section 32);
  a worker or executor restart mid-run resumes from the last persisted stage instead of restarting;
  token budget enforcement test proves a run is failed with `RUN_BUDGET_EXCEEDED` when the cap is
  exceeded.

### Phase A6 — Visualization Service + Dashboard Service (API only)

- Scaffold `apps/visualization-service` (stateless) implementing the ChartSpec JSON Schema
  validator from Section 17 as a pure function + thin FastAPI wrapper
  (`POST /internal/v1/chart-specs/validate`). The validator moves here from `platform-contracts`,
  which keeps only the `ChartSpec` DTO and its exported JSON Schema. analytics-orchestrator's
  `build_chart_spec` repair loop and `validate_chart_spec` step call it; dashboard-service calls it
  again before storing an artifact and before accepting tile `overrides`.
- Scaffold `apps/dashboard-service`; implement DDL from Section 8.6 (`artifacts`, `dashboards`,
  `tiles`, `share_links` table only) with RLS (Section 19). dashboard-service becomes the canonical
  artifact store (Section 8.9): the Flow's `persist_artifact` step writes the artifact through
  dashboard-service's internal API with an id derived from the run, so a resumed run never creates
  a second artifact; the copy in `runs.flow_state` from Phase A5 is removed.
- Public routes: `GET /artifacts/{id}`, `GET /artifacts/{id}/data`, `GET/POST /dashboards`,
  `GET /dashboards/{id}`, `POST /dashboards/{id}/tiles`, `PATCH /tiles/{id}` (Section 9).
  Pinning publishes `dashboard.tile.pinned` (Section 18.1).
- Not in A6: share links and `GET /share/{token}` need tenant sharing policy and the step-up
  middleware (Phase A10); artifact refresh (re-executing `validated_sql` once the result handle
  has expired) and artifact versioning from follow-up instructions (Section 16) need a phase
  assignment before Phase C1.
- **DoD:** Section 32's "first vertical slice" journey (Steps A–D) is provable **entirely over
  HTTP** — `pytest`/Postman drives message → SSE events → `GET /artifacts/{id}` → `POST
  /dashboards/{id}/tiles` — and returns the correct payloads at every step, for a `client`-role
  demo token, with zero browser involved. This is the backend completeness bar Track B's Phase B2
  will build a UI against. Reject an invalid ChartSpec (extra/unknown field) with a test to prove
  the strict schema (Section 17) actually rejects rather than silently drops.

### Phase A7 — Semantic Service

- Scaffold per Section 4.1; implement DDL from Section 8.3 with RLS. Routes: Section 9's
  semantic rows. Metric expressions follow Section 8.3's v1 grammar and are checked against the
  catalog through a new metadata-service internal lookup; metric create/approve/deprecate are
  audited through identity-service.
- metadata-service's agent context packet carries table and column ids (additive), so a metric's
  `base_table_id` or a dimension's `column_id` can be matched to what the agent may see.
- Wire `resolve_semantics` into the Flow (emits `semantic.started/completed`, Section 32). It loads
  the tenant's approved metrics and dimensions from semantic-service, keeps those whose base table
  or column is in the permitted context packet (adding a metric's base table if ranking dropped
  it), and asks the semantic agent to map the request's business terms to them (typed output, ids
  checked against the candidates). A resolved metric fixes the plan's measure deterministically
  (aggregation and column from the definition): the plan and the validated SQL are checked to
  use it, so the model never guesses an aggregation for a defined metric. semantic-service
  unavailable fails the run (`UPSTREAM_UNAVAILABLE`) rather than silently guessing.
- Wire `analyze_result` into the Flow (after `execute_query`, bounded and typed per Section 10.3).
  Before implementing it, decide and record in an ADR what result data the model may see (result
  schema, aggregates, or capped rows), since this is the first stage that exposes query results
  to a model. It runs inside the `visualization` stage (no new event stage).
- Groundedness is tracked per run in `flow_state` (metrics and dimensions used, unmatched terms,
  whether every measure and every number in the insight is grounded) and copied into the
  artifact's `semantic_query`; an eval corpus over the Flow reports it.
- Not in A7: dimension/join-rule edit routes beyond create. Approved joins and metric grammar
  beyond a single aggregate (ratios, filters, multi-table) are post-GA backlog; semantic-lookup
  caching is a Phase C1 hardening requirement.
- **DoD:** a defined "Revenue" metric is used by the chat flow instead of the agent guessing an
  aggregation expression; groundedness eval (Section 25) shows metric usage tracked per run;
  `GET/POST /semantic/metrics` is fully testable over HTTP. (The metric-management UI is Phase B4.)

### Phase A8 — Additional database connectors (MySQL)

- Implement **MySQL 8** end to end behind the existing connector registries
  (`infrastructure/connectors/`, Section 4.1): metadata-service's connectivity test and catalog
  introspection (`information_schema`, only objects the read principal can see), and
  query-gateway's read-only executor. In MySQL a "schema" is a database, so `allowed_schemas`
  lists databases. Core Flow and query-gateway logic do not branch per engine outside the
  connector layer and one dialect lookup.
- **One validator, per-dialect parsing.** query-gateway parses and regenerates SQL in the data
  source's dialect (Section 13). The allow-list is shared, plus a small per-dialect list of
  builtins sqlglot does not model. Engine-specific attacks are added to the unsafe corpus. For
  MySQL: executable `/*! */` comments, `INTO OUTFILE/DUMPFILE`, `@var`/`@@var`, `SLEEP`,
  `BENCHMARK`, `LOAD_FILE`, lock clauses, `HANDLER`/`DO`/`CALL`. Only regenerated SQL ever
  executes, so comments never reach the database.
- **Database-side defense for the new engine, not just the parser:**
  - a read-only transaction and a server-side execution timeout per query;
  - the multi-statement protocol flag off and `LOCAL INFILE` off;
  - a SELECT-only principal;
  - row and byte caps while streaming.
- **The Flow is dialect-aware.** The agent context carries the engine; the SQL generator is told
  the dialect; the metric check (Phase A7) parses in that dialect.
- **Server identity.** Both connectors accept MySQL 8.0+ only, by the handshake's server version.
  MariaDB and TiDB are refused (`UNSUPPORTED_SERVER`); MariaDB, for example, has no
  `max_execution_time`. Proven against a real MariaDB (Section 13.1 standing rule).
- A MySQL twin of `sample-sales-db` (same schema and data) runs in compose for development and
  the live flow.
- **Not in A8:** Snowflake, BigQuery and Redshift connectors. There is no local or CI instance
  to verify them against, so they are post-GA backlog. Their `engine` values stay in the DDL, and
  creating such a data source stays `422 ENGINE_NOT_SUPPORTED`.
- **DoD:**
  - the unsafe-SQL corpus (Section 25), plus the MySQL-specific entries, is 100% rejected in
    both dialects;
  - a write attempted directly through the MySQL executor is refused by the database;
  - the vertical-slice journey (Section 32) succeeds against MySQL, proven over HTTP, including
    an approved metric (Phase A7) applied in MySQL SQL.

### Phase A9 — MCP Gateway (read-only tools first)

- Scaffold `mcp-gateway` per Section 4.1 and implement the DDL from Section 8.7:
  - RLS on every table (`mcp.tools` through its server);
  - `mcp.invocations` append-only for the request role.
- **Registration** (`mcp:manage`). A name, an HTTPS endpoint (no userinfo, query or fragment; an
  IP literal must be public), an optional bearer token (Vault; only `auth_secret_ref` is stored),
  and a declared tool manifest (Section 14). The server is created `pending_approval` without any
  network contact. `developer` holds `mcp:manage` only once granted per user (Phase A10), so in
  this phase `org_admin` registers servers.
- **Approval** (`org_admin`, step-up) is in this phase: the DoD needs an approved server, and
  step-up exists since Phase A1. Approval discovers the tools over MCP Streamable HTTP and
  verifies the manifest (Section 14). On any mismatch or upstream failure the server stays
  pending.
- **Grants** (`org_admin`). Grant a read-class tool to a tenant role or a user, and revoke it.
  Grants on `write`/`admin` tools are refused until Phase A10's per-invocation step-up
  confirmation exists.
- **Invocation proxying** (Section 9, `invoke`):
  - Checks, in order: server approved, tool policy not `deny`, grant. Then call the tool with the
    Section 15 controls applied at request time.
  - Output is capped and returned marked untrusted.
  - Every attempt on a declared tool, allowed or denied, is a row in `mcp.invocations`.
  - A denial is also audited and published as `mcp.invocation.denied`.
- **SSRF suite** (Section 15), all refused:
  - private, loopback, link-local, CGNAT and metadata addresses, including encoded and
    IPv4-mapped forms;
  - hostnames resolving to them;
  - non-HTTPS schemes and URLs with credentials;
  - a redirect (never followed);
  - DNS rebinding between approval and invocation;
  - oversized and wrong-content-type responses.
- **Proven against a real MCP server.** The protocol client is tested against the official MCP
  SDK's Streamable HTTP server, in both its JSON and SSE response modes (Section 13.1's
  live-instance rule, applied to protocols).
- **Not in A9:**
  - `write`/`admin` invocation (Phase A10);
  - `disabled`/`rejected` transitions and the admin console (Phase A10);
  - per-tenant MCP concurrency limits and the dedicated egress proxy (Phase C1);
  - agent use of MCP output in the Flow.
- **DoD** (all proven over HTTP with test tokens):
  - an unapproved MCP server cannot be invoked;
  - an approved server's read tool can be invoked by a `developer` with a grant, and is denied
    for a user without one;
  - a `write` tool is denied even on an approved server;
  - every invocation, including denials, is recorded;
  - the SSRF suite (private-IP, redirect and DNS-rebinding attempts) is 100% blocked.

### Phase A10 — Admin backend, step-up auth, quotas, WebAuthn (API only)

Phase A1 already built most of the admin API: users, roles, invitations, session revocation,
user deletion, audit. It also built step-up (`require_step_up`, and the gateway's step-up
flag). A10 completes authorization:

- **Step-up on every Section 7.3 operation, proven by method.**
  - Sessions record `mfa_verified_method`, and the `Principal` carries whether WebAuthn is
    required. A step-up done with the wrong method is not fresh.
  - New step-up coverage:
    - API-key creation;
    - removing one's own MFA factor, and an admin resetting another user's MFA;
    - enrolling a second factor once one exists (Section 6.6);
    - tenant policy changes;
    - granting and invoking `write`/`admin` MCP tools;
    - reads of query results above the export threshold (`POST /sql/execute`,
      `GET /artifacts/{id}/data`).
  - Tenant deletion has no API (platform operations) and is out of scope.
- **WebAuthn** (identity-service, py_webauthn):
  - enrollment through `POST /auth/mfa/enroll` and `/verify`;
  - step-up through `POST /auth/mfa/challenge` and `/verify`, with single-use challenges bound
    to the session;
  - credential material in Vault (Section 8.1);
  - `platform_super_admin` is refused step-up by any other method.
  - Tested with a software authenticator against the real library.
- **Tenant policies** (identity-service owns "policy mapping", Section 3): `GET/PATCH
  /admin/policies` covers:
  - clients may share dashboards (Section 7.1 "tenant policy");
  - developers may manage MCP servers (Section 7.1 "(if granted)", tenant-wide);
  - `org_admin` must use WebAuthn (Section 6.6).

  The policies feed the effective permissions of every principal (sessions, API keys,
  delegated resolution). `GET /admin/roles` lists the fixed roles.
- **Share links** (dashboard-service, on the Phase A6 table):
  - the dashboard owner creates (step-up), lists and revokes them;
  - the token is 256-bit random, stored hashed, returned once, and time-boxed (at most 7 days);
  - `GET /share/{token}` is public and rate-limited. It returns names, chart specs and chart
    data only: no ids, no SQL, no users.
- **MCP:**
  - `disable` (approved -> disabled, immediate) and `reject` (pending -> rejected);
  - re-approval from `disabled`;
  - `write`/`admin` tools can be granted with step-up and invoked with a fresh step-up;
    `admin` tools by `org_admin` only.
- **Quotas:**
  - The token budget (A5's `ModelRouter`) is readable at `GET /billing/quotas`.
  - Query concurrency becomes a per-tenant limit across replicas (Redis leases in
    query-gateway; a Redis outage falls back to the per-process limit, never to none).
  - A breach is a documented error: `429 QUERY_CONCURRENCY_LIMITED` at the API, and
    `QUERY_CONCURRENCY_LIMITED` for a run after a bounded retry, never `UPSTREAM_UNAVAILABLE`.
- **Public SQL API** (no earlier phase built it, and Phase B3 needs it):
  - `POST /sql/validate`, `POST /sql/execute` (purpose `sql_editor`) and `GET /sql/history`,
    through query-gateway's existing validator and executor;
  - per-connection grants for `sql:execute` (`metadata.data_source_grants`; `org_admin` needs
    none). This replaces Section 7.1's "approval by admin for prod", which has no basis in the
    DDL: data sources carry no environment.
- **Not in A10:**
  - `/admin/webhooks` (Phase A11, with notification-service);
  - a four-eyes rule for semantic approval (post-GA backlog);
  - platform-operator tenant administration.
- **DoD:**
  - every Section 7.3 operation with an API requires a fresh MFA check. An automated test
    first performs it with a stale or absent step-up (expect `403`), then with a fresh one
    (expect success). A TOTP step-up is refused for `platform_super_admin`, and for `org_admin`
    under the WebAuthn policy;
  - a quota breach returns a documented error code, not a silent failure;
  - a share link serves its snapshot until it expires or is revoked, then `404`;
  - a developer without a per-connection grant gets `403` from `/sql/execute`.

  **No admin console UI exists yet — that is Phase B6.**

### Phase A11 — Notification service, webhooks, billing usage (API only)

- Scaffold `apps/notification-service` (port 8010); implement DDL from Section 8.8; wire it as a
  consumer of the topics in Section 18.1 that have `notification-service` listed and a producer:
  - `dashboard.tile.pinned` -> in-app to the pinner;
  - `mcp.invocation.denied` -> in-app and email to every active `org_admin`;
  - `metadata.sync.completed` -> in-app and email to whoever ran the sync;
  - `identity.role.changed` -> in-app and email to the affected user.
  - `query.completed` has no producer yet (see 18.1).
  - Producers added in this phase: metadata-service (sync completion) and identity-service (role
    changes).
  - Recipients and addresses come from an identity-service internal directory endpoint.
  - Consumers are durable JetStream pull consumers. Delivery is idempotent per source event
    (`event_key`).
- Implement `/me/notifications` (list, mark read) and `/admin/webhooks` (create, list, disable;
  moved from A10: notification-service owns webhooks).
  - Webhook delivery is HMAC-SHA256 signed: `X-Buvi-Signature: t=<unix>,v1=<hex>` over
    `"<t>.<body>"`.
  - It uses the Section 15 controls: URL rules at creation; resolution, address check and pinning
    at delivery; no redirects; timeouts.
  - Event types come from a webhook allow-list: `dashboard.tile.pinned`,
    `metadata.sync.completed`, `mcp.invocation.denied`.
  - There are three bounded attempts per delivery; each outcome is recorded.
- Implement `/billing/usage` aggregation from `billing.usage.recorded` events (Section 23).
  - query-gateway starts metering execution time.
  - worker-runtime consumes the events and writes them through analytics-orchestrator
    (`analytics.usage_records`).
  - Seats are read live from identity-service.
- **DoD:** a dashboard pin, a failed MCP invocation, and a sync completion each produce the
  correct in-app/email notification (MailHog inbox checked by test), provable over HTTP/queue
  inspection alone; a subscribed webhook receives a correctly signed payload for an allow-listed
  event type and is rejected for a non-allow-listed destination.

### Phase A12 — Backend completeness gate (exit Track A here)

- Assemble a single automated suite (`scripts/backend-e2e.sh` or a pytest "system" marker) that
  runs the **entire** vertical slice plus admin plus MCP plus notifications, against
  `docker-compose.dev.yml`, using only HTTP/SSE clients and message-queue assertions — no
  browser, no Next.js.
- Run the full Section 24 security-loophole checklist as automated tests where the item is
  testable without a UI (all of them are, by construction, since every control in this document
  is enforced server-side per Section 7.4).
- **DoD (hard gate — do not start Track B until this passes):** the suite is green in CI; every
  service exports a valid OpenAPI doc into `contracts/openapi/`; `scripts/gen-client.sh` produces
  a working generated TypeScript client in `packages/ts/api-client` from those specs. This
  generated client is what Track B will import — Track B therefore starts from a contract that is
  already implemented, already running, and already tested.

---

### Track B — Frontend (Next.js only; backend is frozen except for bug fixes)

### Phase B1 — Real browser auth + design system setup

- Define the design tokens from Section 5.1 as CSS variables / Tailwind theme extension in
  `src/app/globals.css` + `tailwind.config.ts` before building any page — every subsequent Track
  B phase consumes these tokens rather than hardcoding colors/spacing.
- Build the Next.js `(auth)` route group, `proxy.ts` early gate, and the BFF session-cookie flow
  (Section 6.1) against identity-service and api-gateway exactly as they already exist from
  Track A — this phase should require zero backend changes if Phase A1/A12 were done correctly.
- **DoD:** a human logs in through a real browser via Keycloak (full Authorization Code + PKCE,
  not the test-only direct grant from A1), lands on a role-appropriate page, and logs out; the
  session cookie is HttpOnly/Secure/SameSite in the actual browser's dev tools, not just in a test
  assertion; the login page itself already reflects the Section 5.1 palette/typography, not
  default shadcn/Tailwind styling.

### Phase B2 — Client Chat UI + Dashboards

- Build `(client)/chat`: message input, SSE-driven execution trace UI (Section 11's
  `AnalyticsRunEvent` types), ECharts render of the returned `ChartSpec`, "Pin to Dashboard"
  action, `(client)/dashboards` grid view — all calling the generated client from Phase A12.
- **DoD:** Section 32's "first vertical slice" user journey works end-to-end through the real
  browser UI, for a `client`-role demo user, with the execution trace visibly streaming, and no
  backend code needed to change to make it work.

### Phase B3 — Developer SQL Editor

- Build `(developer)/sql` against the same `POST /sql/validate` / `POST /sql/execute` routes —
  no separate execution path from the chat flow's query-gateway integration.
- Add query history view, schema explorer sourced from metadata-service, "Send to Chat/Chart."
- **DoD:** a `developer`-role user can browse the sample-sales-db catalog, write/execute SQL, see
  results, and send a result to the chart flow in the real UI; a `client`-role user gets a
  UI-level "not available" state *and* still gets a real `403` if they hit the route directly
  (Section 7.4 — the UI hiding is cosmetic, the A10-tested `403` is the real control).

### Phase B4 — Semantic management UI

- Build `(developer)/semantic` (or fold into `(developer)/data`) against `GET/POST
  /semantic/metrics` (already complete and tested since Phase A7).
- **DoD:** a `developer` can define/edit a metric in the browser and see the next chat run use it.

### Phase B5 — MCP UI

- Build `(developer)/mcp` (registration form) and the admin approval surface for it against the
  already-complete Phase A9 endpoints.
- **DoD:** registering a server shows `pending_approval` in the UI; approving it (as admin) makes
  its read tools invokable from the developer UI, matching the Phase A9 API test results exactly.

### Phase B6 — Admin console UI

- Build `(admin)/{users,roles,connections,audit,billing}` against the Phase A10/A11 endpoints.
- Wire step-up re-auth prompts in the UI for every action Phase A10 already protects server-side.
- **DoD:** every admin action available in the UI is backed by a passing Phase A10 authorization
  test; there is no admin UI action that lacks a corresponding server-side test from Track A.

### Phase B7 — Notifications UI, guest share-link UI

- Build in-app notification center against Phase A11; build `(guest)/share/[token]` against the
  share-link endpoints (Section 9).
- **DoD:** notifications appear/mark-read correctly; a signed share link opens a read-only
  dashboard snapshot for an unauthenticated visitor and expires correctly.

---

### Track C — Hardening (cross-cutting, both layers)

### Phase C1 — Production hardening pass

- **Entry requirement (before C1 starts):** the `anthropic` model provider in analytics-orchestrator
  has been exercised against the real Anthropic API with a real key — a full run for the Phase A5
  DoD message, primary and fallback models, structured output parsing, refusal and `max_tokens`
  handling, and actual token usage matching the budget ledger — with the result recorded in an ADR.
  Until then it is tested only through the offline scripted provider (ADR 0006).
- **Entry requirement (before C1 starts):** certificate-verified data-source connections
  (`sslmode: verify-full`) work and are proven live for both Postgres and MySQL. This includes a
  per-data-source CA bundle in the secret (managed databases such as RDS and Cloud SQL sign with
  their own CA, which the system trust store does not hold). CI tests run against a TLS-enabled
  instance of each engine, covering:
  - a hostname mismatch is refused;
  - an untrusted CA is refused;
  - `require` still encrypts.

  Today `verify-full` uses only the system trust store and has not been exercised live (ADR 0011).
- Run every item in Section 24's checklist as an explicit test or manual review sign-off recorded
  in `docs/runbooks/security-review-<date>.md`.
- Complete Section 28 (Terraform environments, DR drill), Section 22.1 (SLO dashboards/alerts),
  Section 29 (data export/delete flows).
- Outbound network hardening (Sections 15, 24, 28):
  - mcp-gateway and notification-service egress goes through a dedicated egress proxy, with a
    NetworkPolicy that gives them no other outbound path;
  - MCP invocation gets per-tenant concurrency and per-minute limits.
- Semantic-lookup caching (Section 20): cache semantic-service's approved-definition context and
  the metadata agent-context packet per tenant (and data source) with a TTL and explicit
  invalidation on metric approve/deprecate and catalog sync. Correctness never depends on the
  cache: a miss or an outage falls back to the live call, and an invalidation failure only shortens
  the TTL. Required before C1's DoD.
- **DoD:** Section 38 (Definition of Done for production readiness) is fully satisfied, including
  the semantic-lookup caching item above.

### Phase C2 — Optional: Superset integration

- Only after Phase C1. Evaluate embed vs. API-integration per Section 34; do not rewrite Superset.

### Post-GA backlog (explicitly out of Track A-C; each needs its own spec section before work starts)

- **Warehouse connectors (Snowflake, BigQuery, Redshift).** They need vendor sandbox accounts
  wired into CI (credentials in the secret store), connector modules behind the Phase A8
  registries, the unsafe corpus run in each dialect, and the Section 13.1 standing verification
  rule met against each vendor's sandbox. Until then their `engine` values are refused
  (`ENGINE_NOT_SUPPORTED`).

- **Four-eyes semantic approval:** a tenant policy that stops a metric's creator from approving
  it (ADR 0010). It needs the tenant policy in semantic-service's authorization path.

- **Richer metrics:** ratio metrics (e.g. average order value as revenue / orders), metric-level
  filters, and multi-table metrics over approved `semantic.join_rules`, with join-rule management
  routes and an approval workflow. v1's single-aggregate grammar (Section 8.3) stays valid; any
  extension must keep the Flow's deterministic metric check (ADR 0010) exact, not presence-based.

---

## 32. First vertical slice — exact implementation contract

Scope: one PostgreSQL database, one tenant, real tenant/auth boundaries (never faked "single
tenant mode"). Journey: login → Chat → "Create a sales dashboard for Q2" → retrieve approved
sales tables → generate SQL → validate → execute → line/bar charts → pin to dashboard.

```
Step A — POST /api/v1/conversations/{id}/messages
  in:  { "content": "Create a sales dashboard for Q2" }
  out: { "run_id": "run_01..." }

Step B — GET /api/v1/runs/{run_id}/events   (SSE)
  events, in order (each stage emits started then completed; on failure `<stage>.failed`
  followed by `run.failed`):
    intent.started          intent.completed
    schema.started          schema.completed
    semantic.started        semantic.completed
    sql.started             sql.completed
    validation.started      validation.completed
    execution.started       execution.completed
    visualization.started   visualization.completed
    artifact.started        artifact.completed      (carries artifactId)
    run.completed

Step C — GET /api/v1/artifacts/{artifact_id}
  returns: user-safe summary, chart_spec, result schema, source references,
           refresh metadata, pin capability

Step D — POST /api/v1/dashboards/{dashboard_id}/tiles
  in:  { "artifact_id": "artifact_01..." }
  out: created DashboardTile
```

---

## 33. Code ownership — where does this logic go?

| Task | Correct location |
|---|---|
| Validate API payload | api schema / Pydantic model |
| Decide whether a user can execute SQL | application policy + authorization service |
| Parse/reject unsafe SQL | query-gateway domain/infrastructure validator |
| Call Postgres | connector adapter |
| Tell the agent which tables to use | metadata retrieval + semantic service |
| Create a dashboard tile | dashboard application service |
| Render a chart | Next.js feature/component using a validated ChartSpec |
| Track run progress | analytics orchestrator + event publisher |
| Log a security event | audit subsystem |
| Enforce an SSRF-safe fetch | shared outbound-fetch client in `platform-observability`/dedicated egress module, never ad hoc `httpx.get` |
| Enforce a token budget | `ModelRouter` (analytics-orchestrator), not the individual agent |

Avoid "utils.py" as a dumping ground. Name modules after business responsibility. A helper
belongs near the feature that owns the behavior unless it is genuinely cross-cutting (and if it
is, it belongs in a named `packages/python/platform-*` package with a stable, narrow contract —
not an open-ended "common" package, per Section 34's non-negotiable rules.

---

## 34. Superset relationship

Apache Superset already contains mature database connections, SQL Lab, dashboards, charting, and
a React/TypeScript frontend, and exposes REST APIs. Its architecture is coupled to
Flask-AppBuilder, SQLAlchemy, and its internal extension model. A Flask-to-FastAPI rewrite would
duplicate years of BI capability and create a large migration surface. Use Superset selectively:
embed/reuse capabilities where practical, call its APIs for operational interoperability, or run
it as an internal BI engine while this product owns the AI-native UX. The developer workspace
should feel familiar to Superset users without cloning Superset's entire internal codebase —
implement only the minimum technical UX the product needs: SQL editor, data source management,
charts, dashboards, agent run inspection.

## 35. Competitive differentiation

Natural-language analytics already exists (Metabase Metabot, WrenAI, ThoughtSpot, and others).
WrenAI emphasizes schema-aware retrieval and semantic context; Metabase documents natural-language
chart creation and SQL generation; Superset provides the underlying BI workflows. The
differentiator is therefore not "chat that creates charts" — it is the **governed orchestration
layer**: multi-source data access, MCP governance, semantic definitions, enterprise
authorization, traceable artifacts, and a dual Client/Developer experience with a real Admin
control plane.

**Design principle:** AI should propose; the platform should verify. The closer an operation gets
to credentials, permissions, irreversible changes, or raw data movement, the less freedom the
model should have.

## 36. Final target architecture

```
┌───────────────────────────────────────┐
│          Next.js App Router            │
│   Client | Developer | Admin | Guest   │
│  Chat | Dashboards | SQL | MCP | Audit │
└───────────────────┬────────────────────┘
                     │ HTTPS
            ┌────────▼─────────┐
            │  Next BFF / API   │
            │      Gateway      │
            └────────┬──────────┘
   ┌─────────────────┼──────────────────────┐
   │                 │                       │
Identity/Access  Analytics Orchestrator  Dashboard Service
   │                 │                       │
OIDC / Sessions   CrewAI Flow              Tiles / Artifacts
                     │
     ┌───────────────┼────────────────────┐
     │               │                    │
Metadata Service  Semantic Service   Visualization Service
     │               │                    │
     └───────────────┼────────────────────┘
                      │
               Query Gateway
              /   /   |   \   \
            PG  MySQL Snowflake BigQuery ...
                      │
                  Databases

MCP Gateway        -> approved MCP servers/tools (SSRF-controlled egress)
Worker Runtime      -> event broker -> async jobs (sync, exports, notifications)
Notification Svc    -> email / in-app / signed webhooks
OpenTelemetry        -> logs / metrics / traces, correlated by request_id / run_id
Vault                -> every secret_ref across every service
```

## 37. Non-negotiable engineering rules

- No shared `models.py` database across all services. Each service owns its persistence model
  and API contract.
- No raw DB credentials in frontend code, browser storage, Git, logs, or normal application
  tables — only `secret_ref` pointers into Vault.
- No direct browser access to customer database connections.
- No unrestricted execution of LLM-generated Python/JavaScript.
- No direct LLM-controlled SQL execution — the query gateway always validates and authorizes.
- No authorization based only on frontend route protection.
- No hidden cross-service database reads that bypass the owning service's API.
- No agent logs containing secrets or sensitive raw datasets.
- No "utils" or "common" package for arbitrary code — shared packages have stable, narrow
  contracts.
- No new service without recording: owner, API contract, health checks, metrics, auth policy,
  database/messaging dependencies, tests, and a runbook, before production deployment.
- No role beyond the ones listed in Section 2 without an ADR.
- No outbound fetch on a user's behalf without the SSRF controls in Section 15.

## 38. Definition of Done for production readiness

1. Authentication is standards-based; sessions are secure; logout/revocation works; privileged
   actions require step-up controls (Section 6, 7.3).
2. Every protected endpoint enforces tenant and resource authorization on the server (Section 7).
3. Query execution is isolated behind the query gateway with read-only credentials, limits,
   timeouts, and audit (Section 13).
4. Analytics runs survive worker retries/restarts without losing state (Section 10.2).
5. Chat progress is streamed as user-safe events, never private chain-of-thought (Section 11).
6. Chart rendering accepts only validated ChartSpec data (Section 17).
7. Every database/MCP connection is tenant-scoped and secret-backed (Section 8, 13.1, 14).
8. API and event contracts are versioned and contract-tested (Section 18.1, 26).
9. Logs/traces make one user request traceable across services (Section 22).
10. CI blocks formatting, type, test, and security regressions; deployment is repeatable
    (Section 26).
11. Row-Level Security is enabled on every tenant-owned table (Section 19).
12. Every Section 24 checklist item is signed off (Section 31, Phase C1).
13. Backup/DR is tested, not just configured (Section 28).
14. Data subject export/delete flows work end-to-end (Section 29).

## 39. Senior-engineering verdict

The right build order is not "create FastAPI routes and add CrewAI agents." It is: establish
service and tenant boundaries, secure identity, build the data catalog, build the query gateway,
implement one vertical analytics slice with real tenant/auth boundaries, then widen connectors
and agent capabilities. Next.js owns the user experience; FastAPI owns business APIs and backend
services; CrewAI owns the reasoning workflow inside one orchestrator; the metadata/semantic layer
supplies grounded context; the query gateway controls every database execution; the visualization
layer converts validated results into a safe ChartSpec; the dashboard service persists the
resulting analytics artifacts. This gives the product a credible path from MVP to a multi-tenant
enterprise analytics system without a Flask-to-FastAPI rewrite of Superset, and gives a senior
developer or a coding model the precise, deterministic order of implementation captured in
Section 31.

---

## References

**FastAPI.** Bigger Applications - Multiple Files — https://fastapi.tiangolo.com/tutorial/bigger-applications/
**FastAPI.** Lifespan Events — https://fastapi.tiangolo.com/advanced/events/
**FastAPI.** OAuth2 with Password and JWT — https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/
**FastAPI.** WebSockets — https://fastapi.tiangolo.com/advanced/websockets/
**Next.js.** Project Structure and Organization — https://nextjs.org/docs/app/getting-started/project-structure
**Next.js.** Authentication — https://nextjs.org/docs/app/guides/authentication
**Next.js.** proxy.ts — https://nextjs.org/docs/app/api-reference/file-conventions/proxy
**CrewAI.** Agents and Flows — https://docs.crewai.com/core-concepts/Agents
**uv.** Projects and Workspaces — https://docs.astral.sh/uv/concepts/projects/workspaces/
**uv.** Project init / Python versions — https://docs.astral.sh/uv/concepts/projects/init/ , https://docs.astral.sh/uv/concepts/python-versions/
**SQLAlchemy.** Asyncio Extension — https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
**OWASP.** Authentication Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
**OWASP.** Authorization Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html
**OWASP.** API Security Top 10 (2023) — https://owasp.org/API-Security/editions/2023/en/0x11-t10/
**OWASP.** Password Storage Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
**OWASP.** CSRF Prevention Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
**OWASP.** Server-Side Request Forgery Prevention Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Server-Side_Request_Forgery_Prevention_Cheat_Sheet.html
**OpenTelemetry.** Python — https://opentelemetry.io/docs/languages/python/
**Apache Superset.** REST API Reference — https://superset.apache.org/developer-docs/api/
**Apache Superset.** SQL Lab API — https://superset.apache.org/developer-docs/api/sql-lab/
**Apache Superset.** Database API — https://superset.apache.org/developer-docs/api/database/
**Apache Superset.** Architecture / Extensions — https://superset.apache.org/developer-docs/extensions/architecture/
**Metabase.** Metabot — https://www.metabase.com/docs/latest/ai/metabot
**WrenAI.** GitHub README — https://github.com/Canner/WrenAI
**HashiCorp Vault.** Secrets Engines — https://developer.hashicorp.com/vault/docs/secrets
**NATS.** JetStream — https://docs.nats.io/nats-concepts/jetstream
