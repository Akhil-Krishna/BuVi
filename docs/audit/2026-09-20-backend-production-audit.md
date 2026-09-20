# FastAPI Backend Production Readiness & Security Audit — 2026-09-20

**Scope:** backend only (11 FastAPI services in `apps/*`, shared `packages/python/platform-*`).
The Next.js frontend is Track B and not built; frontend-only checks are marked
**NOT APPLICABLE — FRONTEND NOT COMPLETED**.

**Audited at:** commit `a6a64a5` (Track A complete through Phase A12).

## Architecture established (Phase 1)

| Aspect | Finding |
|---|---|
| Entry points | 11 services, `apps/<svc>/src/<mod>/main.py`, `create_app()` factories. `api-gateway` (:8000) is **the only service the browser/BFF reaches**; the other ten require a signed service token (`require_gateway_token`). |
| AuthN | OIDC Authorization Code + PKCE against Keycloak, **BFF pattern** (Section 6.1): browser talks only to the Next.js origin; the BFF holds an HttpOnly session cookie; no token ever reaches the browser. Service-to-service uses short-lived signed JWTs with audience+scope (Section 6.3). |
| AuthZ | Three-layer: permission (`require_permission`), resource-tenant (`require_resource_owner`, cross-tenant ⇒ **404** not 403), and step-up MFA (`require_step_up`, 5-min freshness, method-aware). |
| Data | Postgres, one schema per owning service, **forced RLS on every tenant table**, request-path role `buvi_app` (non-superuser, no BYPASSRLS, owns no tables). Alembic per service. |
| Secrets | Vault (`platform-secrets`); DB rows store only `secret_ref` pointers. |
| LLM | `ModelRouter` in analytics-orchestrator is the only path to a provider; per-run/per-tenant token budgets enforced *before* each call. |
| Observability | Structured JSON logs, `X-Request-ID` minted at the edge (client value never trusted), `redact()` strips secret-shaped keys. |

---

## Checklist results (Phases 2–4)

| # | Check | Status | Evidence | Risk | Proposed fix |
|---|---|---|---|---|---|
| 1 | Hide API keys | **IMPLEMENTED** | Vault via `platform-secrets`; rows keep `secret_ref` only (`metadata.data_sources`, `mcp.servers`, `notification.webhook_subscriptions`). `redact()` (`platform_observability/logging.py`) strips `password/token/api_key/secret_hash/dsn/authorization/...` before any log write. Live test `test_credentials_never_leave_the_secret_store`. | — | None |
| 2 | Purge Git secrets | **IMPLEMENTED** | `gitleaks detect` over full history: **0 leaks, 75 commits**. Working-tree scan's only hits were Next.js's gitignored `.next/` build cache (`git ls-files .next` = 0 tracked). CI runs `gitleaks-action`. | — | None |
| 3 | Public DB keys | **NOT APPLICABLE** | No public/frontend database credential exists. All DB access is server-side via `buvi_app`; the browser never holds a DB credential (BFF pattern). | — | None |
| 4 | Row-level security | **IMPLEMENTED** | Forced RLS (`relrowsecurity` **and** `relforcerowsecurity`) on all 35+ service tables, verified live by `tests/system/test_live_controls.py::test_every_service_table_has_forced_rls`; `buvi_app` is non-superuser, no BYPASSRLS, owns no tables. Plus app-layer resource-tenant checks. | — | None |
| 5 | Encrypt sensitive data | **IMPLEMENTED** | Passwords live in Keycloak (never in this DB). API keys hashed with **argon2** (`test_api_key_secret_is_hashed_with_argon2`). Session/invitation/share tokens stored as SHA-256 hashes. Customer DB credentials in Vault. TLS in transit. | — | `verify-full` data-source TLS already tracked as C1 entry requirement (ADR 0011) |
| 6 | Server-side authentication | **IMPLEMENTED** | Every non-public catalog route asserted to 401 without credentials and with an invalid one (`test_every_protected_route_requires_authentication`, `test_invalid_credentials_are_rejected`, parametrised over the whole catalog). Internal services refuse non-gateway calls (`test_require_gateway_token_blocks_direct_calls`). | — | None |
| 7 | Lock down record access (IDOR/BOLA) | **IMPLEMENTED** | `require_resource_owner` + authorization-triplet tests per service; cross-tenant IDs return **404** (`test_cross_tenant_and_nonexistent_are_indistinguishable`). RLS is the second, independent layer. | — | None |
| 8 | Prevent field tampering | **PARTIALLY IMPLEMENTED** | Server-controlled fields (`tenant_id`, `actor_user_id`) always come from the authenticated `Principal`, never the body; payload fields consumed explicitly (no `**model_dump()` splat into ORM). **But** 3 of ~14 request models omit `extra="forbid"`: `InvitationCreateRequest`, `RoleChangeRequest`, `ApiKeyCreateRequest` (`identity-service/api/v1/schemas.py`). | **LOW** — not exploitable (Pydantic default `extra="ignore"` drops unknowns before they reach any model/ORM), but inconsistent with the codebase's own convention and weakens defence-in-depth against a future refactor. | Add `model_config = ConfigDict(extra="forbid")` to the three. |
| 9 | Secure session cookies | **IMPLEMENTED** | `HttpOnly`, `Secure`, `SameSite=Lax`, path-scoped; asserted from the raw `Set-Cookie` header in both unit and **live** tests (`check_cookie` in `scripts/test_login.py`). Opaque token, not the session row id. | — | None |
| 10 | Hash passwords | **NOT APPLICABLE (delegated)** | No password authentication in this backend — Keycloak owns credentials (Section 6.1). Keycloak realm has `bruteForceProtected: true`, `failureFactor: 5` (verified live). API-key secrets are argon2-hashed (check 5). | — | None |
| 11 | Rate-limit APIs and login | **IMPLEMENTED** | Redis token buckets, five tiers: strict per-IP `auth` (10 cap, 0.2/s) for login/callback/invitation-accept/MFA-verify, per-IP `public`, per-IP authenticated flood guard, **per-user**, **per-tenant**. Per-account MFA-verify limit in identity (429 `MFA_TOO_MANY_ATTEMPTS`). Keycloak locks accounts after 5 password failures. A fairness bug here was fixed today (ADR 0016). | — | None |
| 12 | Bot protection | **NOT APPLICABLE (by design)** | There is **no public self-service signup or password reset** — users arrive only by `org_admin` invitation, and credential flows belong to Keycloak. The public surface is `/auth/login`, `/auth/callback`, `/invitations/{token}/accept`, `/share/{token}` — all rate-limited per IP, with Keycloak's own brute-force lockout behind them. Audit guidance: "Do not blindly add CAPTCHA to internal APIs." | — | None |
| 13 | Parameterize queries | **IMPLEMENTED** | SQLAlchemy Core/ORM constructs + asyncpg bound parameters throughout. No f-string SQL in any request path (one `# noqa: S608` in the offline *scripted test provider*, not a live path). Customer SQL additionally passes the AST allow-list validator (100% unsafe-corpus rejection, `test_one_hundred_percent_of_the_unsafe_corpus_is_rejected`). | — | None |
| 14 | Validate all input | **IMPLEMENTED** | Pydantic v2 everywhere; bounded `Query(ge=..., le=...)` on every paginated endpoint; body size cap (1 MiB, 413); `max_length` on free-text; strict ChartSpec (`extra="forbid"`, no coercion); cursor parsing rejects malformed values (422 `INVALID_CURSOR`). Minor gap = check 8. | — | See check 8 |
| 15 | Escape/sanitize UGC | **IMPLEMENTED (backend obligations)** | Backend returns JSON only — it generates no HTML/templates. ChartSpec rejects markup-bearing/unknown fields (`test_unknown_keys_and_markup_are_rejected`). Error details never echo attacker input (`test_rejection_details_are_identifiers_never_parser_text`). Frontend escaping = **NOT APPLICABLE — FRONTEND NOT COMPLETED**. | — | None |
| 16 | Restrict file uploads | **NOT APPLICABLE** | No upload endpoints exist (`UploadFile`/`File(`/multipart: zero occurrences outside tests). | — | None |
| 17 | Limit upload size | **NOT APPLICABLE** | No uploads. A global 1 MiB request-body cap exists regardless. | — | None |
| 18 | Trim API responses | **IMPLEMENTED** | Explicit `response_model` on routes; secrets shown once and never re-listed (API key, webhook signing secret, share token) with `Cache-Control: no-store`; guest share snapshot asserted to contain no SQL/result handles/user ids/tenant ids. | — | None |
| 19 | Security headers | **MISSING** | No `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, or default `Cache-Control` at the edge. Only `dashboard-service/api/v1/share_links.py` sets them, for its one guest route. No reverse-proxy/ingress config is committed to own them (Terraform is a C1 deliverable), so **nothing currently sets them**. | **MEDIUM** — defence-in-depth gap on the internet-facing edge; JSON responses carrying tenant data are cacheable by intermediaries by default. | Small response-header middleware at api-gateway that sets defaults **without** overriding route-set values. HSTS/CSP stay with the TLS edge/frontend. |
| 20 | Force HTTPS | **NOT APPLICABLE (deployment)** | Section 28 terminates TLS at the ingress/edge; the app correctly does not terminate TLS. Cookies are already `Secure`. Gateway trusts `X-Forwarded-For` only for a configured hop count (`trusted_proxy_hops`, default 0 = gateway is the edge). | Deployment-time | Ingress + HSTS at edge (C1 Terraform) |
| 21 | Scan dependencies | **IMPLEMENTED** | Ran now: `uv export … && pip-audit` ⇒ **"No known vulnerabilities found, 5 ignored"** (the documented chromadb advisories, ADR 0006, whose expiry CI enforces). `npm audit` (npm 11) ⇒ 0 vulnerabilities. Both run in CI. | — | Container image scanning is an explicit C1 deferral |
| 22 | API usage limits | **IMPLEMENTED** | 1 MiB body cap; bounded pagination (`le=200`); per-tenant **query concurrency** via Redis leases (429 `QUERY_CONCURRENCY_LIMITED`); row/byte caps + server-side `statement_timeout` on customer queries; LLM per-run/per-tenant token budgets; MCP argument-size cap and response-size cap. | — | Per-tenant MCP invocation limits = explicit C1 deferral |
| 23 | Spending/cost caps | **IMPLEMENTED** | `ModelRouter` enforces per-run and per-tenant-daily token budgets **before** each provider call and **fails closed** if the ledger is unreadable (`test_unreadable_ledger_fails_closed`). `GET /billing/quotas` exposes the enforced number; `/billing/usage` aggregates metered spend. | — | Provider-side spend alerts remain an operational task |
| 24 | Comprehensive error handling | **IMPLEMENTED** | One envelope (`{error:{code,message,request_id,details}}`) installed in every service via `install_error_handlers`; stable `code` values; stack traces/SQL/credentials never returned. Unhandled exceptions become `500 INTERNAL_ERROR` with the request id only. | — | None |
| 25 | Loading states | **NOT APPLICABLE — FRONTEND ONLY** | Backend behaves predictably under in-flight requests; SSE provides progress for long runs. | — | None |
| 26 | Empty states | **NOT APPLICABLE — FRONTEND ONLY** | Backend obligation verified: collection endpoints return `{"items": []}` with 200, never null/404. | — | None |
| 27 | Handle failed requests | **IMPLEMENTED** | Correct status semantics throughout (401/403/404/409/413/422/429/501/502/503/504), consistent schema, documented codes; upstream failure ⇒ 502, upstream timeout ⇒ 504. | — | None |
| 28 | API timeouts | **IMPLEMENTED** | Every outbound `httpx.AsyncClient` sets explicit total **and** connect timeouts (verified across all 11 services). Customer DB connections set `statement_timeout` + `idle_in_transaction_session_timeout` + connect timeout. Bounded retries with backoff (query capacity 3×, webhooks 3 attempts, MCP none) — no retry storms, no infinite waits. | — | Platform-DB `lock_timeout` already tracked as a C1 entry requirement (ADR 0014) |
| 29 | Duplicate subscriptions | **NOT APPLICABLE** | No subscription feature; `POST /billing/subscription` is a documented `501` (post-GA backlog). | — | None |
| 30 | Duplicate payments | **NOT APPLICABLE** | No payment processing anywhere in the backend. | — | None |
| 31 | Optimize DB queries | **IMPLEMENTED** | No N+1 found: batched `IN (...)` loads (e.g. catalog columns for all tables in one query), keyset pagination, `for_update` only where a row lock is genuinely required, short transactions that commit before slow network calls. | — | None |
| 32 | Database indexes | **IMPLEMENTED** | Every tenant-scoped table carries a tenant index; token/lookup columns indexed (`idx_sessions_token_hash`, `idx_invitations_token_hash`, `idx_share_links_token_hash`, `idx_api_keys_prefix`); composite indexes match actual filters (`idx_runs_tenant_status`, `idx_usage_records_tenant_time`, `idx_notifications_inbox` partial on `channel='in_app'`); uniqueness enforced by constraints. | — | None |
| 33 | Paginate large results | **IMPLEMENTED** | Every collection endpoint is cursor-paginated with a bounded `limit` (`ge=1, le=200`, default 50) applied at the database. | — | None |
| 34 | Compress files | **NOT APPLICABLE (deployment)** | Deliberately **not** added in-app: the gateway streams `text/event-stream` (SSE) for run events, and Starlette's `GZipMiddleware` buffers/interferes with streaming responses. Compression belongs to the ingress/CDN (Section 28). | Deployment-time | Enable gzip/brotli at ingress (C1) |
| 35 | Cache repeated requests | **IMPLEMENTED (correctly scoped)** | Only one data cache: the data-source policy cache, keyed by **`(tenant_id, data_source_id)`** with a TTL — no cross-tenant key collision possible. Authorization data is explicitly **never** cached ("a revocation applies to the next query"). | — | Semantic-lookup caching already scheduled for C1 |
| 36 | Uptime monitoring | **PARTIALLY IMPLEMENTED** | `/health/live` and `/health/ready` on all 11 services; readiness genuinely checks dependencies (Postgres/Redis/NATS/Vault/MinIO/upstreams) and degrades rather than lying; Docker `HEALTHCHECK` in every image; OTel collector in compose. External uptime monitoring/alerting is infrastructure (Section 22.1 SLO dashboards = C1). | **LOW** — app-side is complete; alerting is deployment-owned and tracked | None in app code |
| 37 | Error logging | **IMPLEMENTED** | Structured JSON logs, one object per line, `request_id` on every line, `run_id` for analytics runs; auth/security events audited durably in `identity.audit_events` (failure audits committed in an independent transaction so a rollback can't erase them); `redact()` enforced. Rotation/retention is platform-owned. | — | None |
| 38 | Concurrency & backup/restore | **PARTIALLY IMPLEMENTED** | Concurrency **tested**: per-tenant concurrency caps across replicas, pool-exhaustion, idempotency-key replay/conflict, crash-mid-run resume, duplicate-event redelivery, advisory-lock single-flight. Backup/restore: **no backup infrastructure exists yet** (managed Postgres PITR is Terraform/C1), and `docs/runbooks/dr-restore-drill.md` referenced by Section 28 does not exist. No restore test has been performed, and none is claimed. | **MEDIUM (deployment)** — genuine production gap, but not fixable in application code | C1: provision PITR + write and execute the restore drill |

---

## CRITICAL

**None.** No issue found that allows unauthenticated data access, cross-tenant access, credential exposure, or remote code execution.

## HIGH

**None.** The controls that would normally produce HIGH findings in a "vibe-coded" backend — authentication on every route, object-level authorization, tenant isolation, SQL injection defence, secret handling, rate limiting — are implemented *and* backed by executable tests.

## MEDIUM

1. **Interactive API docs exposed unauthenticated (check 6/18/19).** FastAPI's default `/docs`, `/redoc` and `/openapi.json` are served by all 11 services, including the internet-facing gateway, with no auth and no environment gate. They also sit **outside** the catalog-driven pipeline, so they bypass the rate limiter. The schema itself is not secret (it is committed under `contracts/`), so this is reconnaissance surface and third-party CDN script inclusion on the public edge rather than data exposure.
2. **No security response headers at the edge (check 19).** Nothing sets `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` or a default `Cache-Control` for tenant-bearing JSON, and no reverse-proxy config is committed to own them.
3. **No tested backup/restore (check 38).** Deployment-owned and already a C1 deliverable; recorded here because production readiness genuinely depends on it.

## LOW

1. **Three request models omit `extra="forbid"` (check 8).** Not exploitable today; an inconsistency with the codebase's own convention.
2. **External uptime monitoring/alerting absent (check 36).** App-side health is complete; alerting is infrastructure.

## NOT APPLICABLE

- **Frontend-only:** 25 (loading states), 26 (empty-state UI). Backend obligations behind both were verified.
- **Feature absent:** 16, 17 (no uploads), 29 (no subscriptions), 30 (no payments), 3 (no public DB credential), 10 (no local password auth — Keycloak owns it), 12 (no public signup/reset to protect).
- **Deployment-owned:** 20 (TLS at ingress), 34 (compression at ingress/CDN).

---

# Phase 5 — Fixes implemented

Three findings were code-fixable. Everything else was already implemented, genuinely not
applicable, or deployment-owned (see "Remaining risks" below). No finding was closed by
weakening a control, and no API contract broke.

## Fix 1 — Gate interactive API docs by environment (MEDIUM #1)

**What changed.** A new helper, `docs_routes(environment)`, in
`packages/python/platform-observability/src/platform_observability/app.py`, returns `{}` in
`dev`/`test` and `{"docs_url": None, "redoc_url": None, "openapi_url": None}` beyond them. All
11 services spread it into their `FastAPI(...)` construction.

**Why.** `/docs`, `/redoc` and `/openapi.json` were served unauthenticated by every service,
including the internet-facing gateway, and sat outside the catalog-driven pipeline so they
bypassed the rate limiter.

**Why a helper rather than per-service literals.** The decision is one rule, so it lives in one
place; a new service gets the behaviour by using the same constructor spread as the other ten.

**Contract impact.** None. `app.openapi()` still works in every environment, so
`scripts/gen-contracts.sh` and the committed `contracts/openapi/*.json` are unaffected — only
the *HTTP route* is withdrawn, not the schema. Verified in both environments.

**Frontend impact.** None (the BFF never reads `/openapi.json` at runtime; the TypeScript client
is generated at build time from `contracts/`).

## Fix 2 — Security response headers at the edge (MEDIUM #2)

**What changed.** New `apps/api-gateway/src/api_gateway/api/security_headers.py` adds
`SecurityHeadersMiddleware`, wired in `main.py`. It sets `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and `Cache-Control: no-store`.

**Deliberate exclusions, with reasons:**
- **HSTS** — the app cannot know whether TLS was terminated in front of it; emitting HSTS from a
  plaintext origin is wrong. It belongs to the TLS edge (C1).
- **CSP** — the gateway returns JSON and SSE, never a document. A CSP here protects nothing and
  would be a misleading signal. The BFF origin that serves HTML owns CSP.

**Implementation note.** Written as raw ASGI rather than `BaseHTTPMiddleware` because
`BaseHTTPMiddleware` buffers responses, which would break the `text/event-stream` run-event
stream. It uses set-default semantics: an upstream that deliberately sets one of these headers
keeps its value.

**Contract impact.** Response headers only; no body or status change.

## Fix 3 — `extra="forbid"` on three request models (LOW #1)

**What changed.** `model_config = ConfigDict(extra="forbid")` on `InvitationCreateRequest`,
`RoleChangeRequest` and `ApiKeyCreateRequest` in
`apps/identity-service/src/identity_service/api/v1/schemas.py`.

**Why.** Defence in depth against mass assignment. These endpoints already derive `tenant_id`,
`user_id` and `created_by` server-side, so this was not exploitable — but it makes a client that
*tries* to set them fail loudly at 422 instead of silently succeeding with the field ignored.

**Contract impact.** `additionalProperties: false` appears on three schemas in
`contracts/openapi/identity-service.json` and `api-gateway.json`.
`scripts/diff-contracts.sh` reports **no breaking changes**; the generated TypeScript client is
byte-identical (openapi-typescript does not represent `additionalProperties: false`).

---

# Phase 6 — Tests executed

Every command below was actually run on 2026-09-20; results are quoted verbatim.

| Command | Result | Detail |
| --- | --- | --- |
| `make lint` | **PASS** | `ruff check` all checks passed; `ruff format --check` 639 files already formatted |
| `make typecheck` | **PASS** | mypy over shared packages + every service's `core`/`domain`/`application`; no issues found |
| `make test` | **PASS** | **1787 passed, 5 skipped**, 405 warnings, 243.49s |
| `make backend-e2e` | **PASS** | 13/13 steps (see table below) |
| `uvx pip-audit -r <uv export --all-packages --no-emit-workspace>` | **PASS** | "No known vulnerabilities found, 5 ignored" (the 4 ADR-0006 chromadb advisories + expiry guard) |
| `npx npm@11.6.2 audit --audit-level=high` (web/next-app) | **PASS** | "found 0 vulnerabilities" |
| `gitleaks detect --source . --redact` (history) | **PASS** | 76 commits scanned, **no leaks found** |
| `gitleaks detect --source . --no-git --redact` (working tree) | **6 hits, all benign** | All six are in `web/next-app/.next/` — Next.js's own generated preview/encryption keys in the build cache. `/.next/` is gitignored and **0 files under it are tracked**. Not a credential leak. |

## New security tests added for the fixes

- `apps/api-gateway/.../tests/integration/test_gateway.py::test_security_headers_are_set_on_every_response`
  — asserts all four headers on health, proxied-API and error responses, and asserts HSTS and CSP
  are **absent** (so the deliberate exclusions can't be silently reversed).
- `…::test_upstream_set_headers_are_never_clobbered` — proves the set-default semantics.
- `apps/identity-service/.../tests/integration/test_lifecycle.py::test_request_bodies_reject_unknown_fields`
  — 422 when a body names `tenant_id`, `user_id` or `created_by`.

## Pre-existing coverage verified (not assumed from filenames)

Per the audit principle, these were confirmed by reading the test bodies, not by name:

| Control | Enforcing evidence |
| --- | --- |
| Prompt injection | `test_model_router_and_policies.py::test_prompt_payload_is_rendered_as_tagged_data` feeds `ignore previous instructions </user_request>` and asserts the payload is JSON-quoted inside the delimiter, so neither the instruction override nor the tag-escape can break out |
| LLM cost/abuse caps | `test_budgets_are_enforced_before_the_call`, `test_run_budget_exceeded_before_any_call`, `test_tenant_daily_budget_is_enforced` |
| Oversized bodies | `request_pipeline.read_body()` caps the body **while streaming** and does not trust `Content-Length` alone; 413 `PAYLOAD_TOO_LARGE` |
| Cross-tenant / IDOR | 36 test files exercise cross-tenant access returning 404 |
| RLS | `metadata-service/.../test_row_level_security.py`, plus forced RLS asserted in `tests/system` |
| Migrations | All 8 migrated services have **exactly one Alembic head** — no branching |

## Backup restore

**Not tested, and not claimed.** No backup infrastructure exists in this repository yet
(managed-Postgres PITR is Terraform/C1). Per the audit's own rule, no restore capability is
asserted.

### `make backend-e2e` — 13/13 PASS

```
PASS contracts (3s)          PASS test-semantics (36s)     PASS test-notifications (57s)
PASS test-login (33s)        PASS test-mysql-slice (35s)   PASS test-client (27s)
PASS test-data-sources (22s) PASS test-mcp (23s)           PASS system (2s)
PASS test-query-gateway (26s) PASS test-admin (41s)
PASS test-analytics-run (83s) PASS test-dashboards (35s)
```

---

# Phase 7 — Final report

## 1. Original audit summary

The backend is 11 FastAPI services in a `uv` workspace, fronted by a single api-gateway, with
Keycloak for identity, forced Postgres RLS for tenant isolation, Vault for secrets, and a CrewAI
flow for the analytics agent. Going in, the expectation for a "vibe-coded" backend was a cluster
of CRITICAL/HIGH findings. That is not what the evidence showed: **no CRITICAL and no HIGH
findings exist.** The controls that usually fail — authentication on every route, object-level
authorization, tenant isolation, SQL-injection defence, secret handling, rate limiting — are
implemented *and* backed by executable tests that fail when the control is removed.

What the audit did find were three real but bounded gaps (two MEDIUM, one LOW) plus a set of
genuinely deployment-owned risks that application code cannot close.

## 2. Items verified as implemented

31 of 38 checks: 1, 2, 4, 5, 6, 7, 9, 11, 13, 14, 15, 18, 21, 22, 23, 24, 27, 28, 31, 32, 33, 35,
37 — plus the backend obligations sitting behind the frontend-only checks 25 and 26 (predictable
timeout/error behaviour; collection endpoints return valid empty arrays).

## 3. Items partially implemented (and what is still missing)

- **#36 Uptime monitoring** — app side is complete (`/health/live`, `/health/ready` on all 11
  services, dependency-aware readiness, Docker `HEALTHCHECK`, OTel collector). Missing: an
  external monitor and alert routing. Infrastructure, tracked for C1.
- **#38 Concurrency & backup/restore** — concurrency is thoroughly tested. Missing: backup
  infrastructure and an executed restore drill. Tracked for C1.

## 4. Items fixed

| Fix | Severity | Change |
| --- | --- | --- |
| Docs exposure | MEDIUM | `docs_routes()` gates `/docs`, `/redoc`, `/openapi.json` to dev/test across all 11 services |
| Security headers | MEDIUM | `SecurityHeadersMiddleware` on the gateway: nosniff, DENY, no-referrer, no-store |
| Mass-assignment hardening | LOW | `extra="forbid"` on three identity request models |

## 5. Items intentionally not implemented, with reasons

- **CORS (#19/Phase 3)** — *genuinely* not applicable, and deliberately left absent. The browser
  talks only to the Next.js origin; the BFF holds the HttpOnly session cookie and proxies
  server-side, so no browser origin ever calls api-gateway. Adding permissive CORS would weaken
  the cookie design to solve a problem that does not exist.
- **HSTS / CSP in app code** — TLS-edge and HTML-origin responsibilities respectively; see Fix 2.
- **Compression (#34)** — would break the SSE run-event stream if added in-app; belongs at ingress.
- **TLS termination (#20)** — reverse-proxy/ingress responsibility.
- **Bot protection (#12)** — no public signup or password-reset endpoint exists to protect;
  the audit's own rule says not to add CAPTCHA to internal APIs.
- **Password hashing (#10)** — Keycloak owns credentials; the backend never sees a password.
- **Uploads (#16/#17)**, **subscriptions (#29)**, **payments (#30)**, **public DB credentials (#3)**
  — these features do not exist in this backend.
- **Additional caching (#35)** — the audit forbids caching without proving safety; the one
  existing cache is tenant-keyed and authorization is explicitly never cached.

## 6. Files changed

```
NEW  packages/python/platform-observability/src/platform_observability/app.py
NEW  apps/api-gateway/src/api_gateway/api/security_headers.py
NEW  docs/audit/2026-09-20-backend-production-audit.md
MOD  packages/python/platform-observability/src/platform_observability/__init__.py
MOD  apps/{analytics-orchestrator,api-gateway,dashboard-service,identity-service,mcp-gateway,
      metadata-service,notification-service,query-gateway,semantic-service,
      visualization-service,worker-runtime}/src/*/main.py            (11 files)
MOD  apps/identity-service/src/identity_service/api/v1/schemas.py
MOD  apps/api-gateway/src/api_gateway/tests/conftest.py
MOD  apps/api-gateway/src/api_gateway/tests/integration/test_gateway.py
MOD  apps/identity-service/src/identity_service/tests/integration/test_lifecycle.py
MOD  contracts/openapi/api-gateway.json
MOD  contracts/openapi/identity-service.json
```

## 7. Dependencies added/removed

**None.** All three fixes use FastAPI/Pydantic/Starlette primitives already in the workspace.

## 8. Database migrations required

**None.** No fix touched the schema. (Verified separately: all 8 migrated services have exactly
one Alembic head — no branching.)

## 9. Environment variables required

**No new variables.** Fix 1 reads the existing `environment` setting (`dev` | `test` | anything
else). The only operational requirement is that **production must not set `environment` to `dev`
or `test`**, or the docs routes will be served. That value is already managed as normal
deployment configuration and is not a secret.

## 10. Deployment changes required

| Area | Requirement | Owner |
| --- | --- | --- |
| TLS | Terminate at ingress; redirect 80→443; add HSTS there | C1 |
| Compression | Enable gzip/brotli at ingress, exempting `text/event-stream` | C1 |
| Environment | Confirm `environment` is not `dev`/`test` in prod (now security-relevant) | Deploy |
| Monitoring | External uptime checks against `/health/ready` + alert routing | C1 |
| Backups | Managed Postgres PITR + an **executed** restore drill | C1 |
| Containers | Image scanning, signing, digest pinning | C1 |
| Secrets | Vault already in use; no new secrets introduced | — |

## 11. Tests executed and results

See the Phase 6 table above. Summary: `make lint` PASS · `make typecheck` PASS ·
`make test` **1787 passed, 5 skipped** · `make backend-e2e` **13/13 PASS** ·
`pip-audit` no known vulnerabilities (5 ignored per ADR 0006) · `npm audit` 0 vulnerabilities ·
`gitleaks` no leaks in 76 commits. No test was skipped, weakened, or disabled to make a fix pass.

## 12. Remaining production risks

These require human decision or infrastructure and are **not** closed by this audit:

1. **No disaster-recovery capability has been demonstrated.** No PITR, no restore drill. This is
   the single largest remaining risk: the data is only as safe as an untested assumption.
2. **No TLS/HSTS in the committed deployment configuration.** Until ingress is provisioned, the
   transport security of production is unverified by this repository.
3. **No external alerting.** Readiness endpoints exist but nothing is watching them, so an
   outage is detected by users first.
4. **Container supply chain unaddressed** — no image scanning, signing, or digest pinning.
5. **The A5 crash-resume stall is unreproduced** (1 failure in 15 runs, root cause unknown). It
   is a C1 entry requirement; treat it as an open correctness question, not flakiness.
6. **Accepted dependency advisories.** Four chromadb advisories are ignored because the platform
   never runs Chroma's HTTP server. Correct today; re-review on any crewai/chromadb bump (CI
   enforces this).
7. **Third-party LLM data flow.** Prompts carry tenant business data to Anthropic. Structurally
   defended (tagged data blocks, PII exclusion, budgets) but the data-sharing decision itself is
   a business/compliance call, not a technical one.
8. **`web/next-app/AGENTS.md` has an uncommitted change containing a high-entropy,
   credential-shaped string.** `gitleaks` does not flag it and it is **not** in git history, but
   it was not inspected during this audit. **Review it before committing that file.**
9. Remaining C1 items already tracked: OIDC federation, per-tenant MCP limits, platform-DB
   `lock_timeout`, tenant-deletion cascade, certificate-verified MySQL TLS.

## 13. Final backend checklist

| Item | State |
| --- | --- |
| Authentication enforced on every non-public route | ✅ verified by test |
| Object-level authorization + tenant isolation (cross-tenant → 404) | ✅ verified by test |
| Forced RLS on all service tables; app role has no BYPASSRLS | ✅ verified by test |
| No credential in code, logs, errors, responses, or git history | ✅ scanned |
| SQL injection defence (parsed validator, not pattern matching) | ✅ verified by test |
| Input validation, mass-assignment defence, body-size caps | ✅ verified by test |
| Rate limiting incl. per-user/per-tenant fairness | ✅ verified by test |
| Structured errors, no stack traces or SQL to clients | ✅ verified by test |
| Timeouts, retries, concurrency and cost caps | ✅ verified by test |
| Security response headers | ✅ added by this audit |
| API docs not publicly exposed in production | ✅ added by this audit |
| Dependency + secret scanning clean | ✅ actually run |
| Full suite + 13-step e2e green | ✅ actually run |
| TLS / HSTS at the edge | ❌ deployment, C1 |
| Backup + **tested** restore | ❌ does not exist |
| External uptime alerting | ❌ deployment, C1 |
| Container image scanning / signing | ❌ deployment, C1 |
| A5 crash-resume stall root cause | ❌ open |

**Conclusion.** The *application layer* of this backend is in good shape: every check that
application code can own is implemented and enforced by a test that fails when the control is
removed. This audit deliberately does **not** describe the backend as production ready. Four of
the eighteen items above are unmet, one of them — untested backup restore — is a risk to data
durability that no amount of application code can compensate for, and the A5 stall is an
unexplained correctness signal. Those must be closed by Phase C1 before a production date is set.
