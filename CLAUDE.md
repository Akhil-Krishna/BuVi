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
Track B was replanned against the actual Track A surface and the 15 Stitch reference screens
before any frontend code was written (ADR 0017; spec Sections 5.2, 31 Track B). 7 phases became 8
— Data Sources and SQL Lab split apart, matching two separate Stitch screens and two genuinely
distinct pieces of the developer workspace.

**Phase B1 (Auth, design system, app shell) is complete** — real Authorization Code + PKCE
through an actual browser, Playwright-verified end to end (`web/next-app/e2e/login.spec.ts`):
Keycloak's own login form, a real callback landing page (not identity-service's own `204`),
session-cookie flags read from the browser's cookie jar, and the correct nav variant for a
`client` vs. a `developer` session. Investigating the real login architecture surfaced a spec/
code divergence — identity-service is the OIDC relying party, not the frontend — closed with a
small, additive, allow-listed `redirect_uri` change (ADR 0018) that left the existing 257-test
identity-service suite and `scripts/test-login.sh` unaffected. `src/proxy.ts` (Next.js 16 renamed
`middleware.ts`), the two auth relay routes, the generic BFF proxy, TOTP/WebAuthn MFA
verification, and the role-permission-driven `TopNav` are all in `web/next-app/src`.
Account settings (`/account`) cover the caller's own sessions with self-revoke, MFA
factor management (TOTP + WebAuthn enrollment), and API keys (list/create/revoke, secret shown
exactly once) — all three parts of the account-settings panel Section 5.2 scoped to B1 are now
built. WebAuthn is covered in a real browser through CDP virtual authenticators — enrol, sign
out, sign back in, satisfy the gate — with the negative case too. Minting a key requires a fresh
step-up (Section 7.3); the same reusable `MfaVerifyForm` handles it inline, and a session with no
enrolled factor at all is told to enrol one rather than shown a bare refusal. B1 is now fully
closed. Not done in B1: the `(client)/(developer)/(admin)` route groups' real page content --
that's B2 onward, and B1's own landing page is deliberately minimal.

**Phase B2 (Client chat UI + dashboards) is complete** — Section 32's first vertical slice, proven
end to end in a real browser (`web/next-app/e2e/chat-and-dashboards.spec.ts`): a chat message
streams a live 6-step execution trace over real SSE (`useRunStream`, native `EventSource` through
the BFF proxy), renders the resulting `ChartSpec` with direct `echarts` (no wrapper package --
`echarts-for-react` has no React 19 peer range yet), pins the artifact to a new or existing
dashboard, and the dashboard grid/detail pages render its tiles on the same 12-column grid
`dashboard-service` positions them on. The Track B prerequisite (Section 11's `run.cancelled` wire
event) is closed as a small, additive backend change — `AnalyticsRunEvent.status` gained
`"cancelled"`, `run_executor.py`/`conversation_service.py` emit it instead of overloading
`run.failed`, migration `0003_run_events_cancelled_status` widens the DB check constraint — so a
cancellation is now told apart from a real failure by typed status alone, not the fragile
message-string match the spec's Phase B2 text anticipated as a fallback. `dashboard:share`'s
step-up gate reuses the same `MfaVerifyForm` pattern API keys already established. Chat has no
conversation-history sidebar the Stitch mock shows: there is no `GET /conversations` list endpoint
(Section 9's chat surface is create-and-post only), so each browser tab's conversation is created
lazily and lives only for that tab — the same class of "mock shows more than the backend
implements" gap as B7's Usage & Quotas caveat, not an oversight. Phase B3 (`(developer)/data`) does
not exist yet, so the chat vertical slice's own precondition (an active data source) is provisioned
by `scripts/provision_demo_data_source.py`, called from the spec's own `beforeAll` — idempotent,
mirrors `scripts/test_analytics_run.py`'s `admin_with_sample_sales()`.

**Phase B3 (Data sources & catalog) is complete** — proven end to end in a real browser
(`web/next-app/e2e/data-sources.spec.ts`): a `developer` connects a Postgres data source, sees it
stay `pending` through credential entry (`data_source_service.py`'s `set_secret` deliberately does
not activate it — only a successful test does), tests it, syncs its catalog, and browses tables and
columns; an `org_admin` grants and revokes a specific tenant user's `sql:execute` on that one
connection; a `client`-role session never reaches the page. Zero backend changes this phase — the
full API surface (`metadata-service`) was already built and tested since Phase A3. Built as one
page with inline per-row expansion (list/create/secret/test/sync/tables/grants), not nested routes,
matching Section 4.2's file tree literally (`(developer)/data/page.tsx` only, unlike `(client)`'s
tree which explicitly listed a `dashboards/[dashboardId]` route for B2).

Two real reactivity bugs caught by testing, both the same class: a value fetched once by a client
component on row-expand (the table list, the sql-grant list) never re-fetched after a mutation
changed it, because `router.refresh()` only re-renders server-component props — it does nothing for
state a client component fetched itself. Fixed by keying `TableBrowser` on `last_sync_at` (remounts
-> refetches after a sync) and giving `SqlGrantsPanel` an explicit `onChanged` callback instead of
relying on `router.refresh()`. Also caught before writing any test: Test/Sync were wired disabled
while `status === "pending"`, but testing is how a connection *leaves* pending in the first place —
whatever function requires a resource to already be in the state it produces can never be reached.

**Phase B4 (SQL Lab) is complete** — proven end to end in a real browser (`web/next-app/e2e/
sql-lab.spec.ts`): a `developer` browses the catalog (B3's table browser, reused as-is, not
rebuilt), writes and runs SQL in a real CodeMirror 6 editor (`@uiw/react-codemirror` +
`@codemirror/lang-sql`, line numbers and SQL syntax highlighting themed from this app's own
tokens, not a bundled dark theme) against `query-gateway`'s real validate/execute/history
endpoints — the exact same validator and executor the chat flow uses, never a second path — sees
results, and sends one to the chart flow; running with a row limit above `export_step_up_rows`
(10,000 by default) prompts step-up in the browser, matching `query_service.py`'s server check
exactly; a `client`-role session never reaches the page. "Send to Chat/Chart" is an honest handoff,
not a fake shortcut: chat has no endpoint that accepts injected SQL or result rows (Section 9's
chat surface is `content` + optional `data_source_id` only), so it seeds `/chat`'s composer with
the same data source and a natural-language prompt and lets the real chat pipeline regenerate SQL
from scratch — `ChatPanel` gained `initialPrompt`/`initialDataSourceId` props for exactly this,
reachable only via `/chat?prompt=&dataSourceId=` and otherwise inert. A true skip-regeneration
endpoint was investigated directly against `run_executor.py`/`analytics_flow.py` and found
non-trivial (it touches `_authorize_query`'s real Section 7.2 check and Section 32's hardcoded
9-stage SSE sequence, not just UI) — tracked as a named Phase C1 candidate, not built here
(ADR 0020).

Two real test-idempotency bugs caught while writing `sql-lab.spec.ts`, both about async client
state that isn't a server prop: the SQL grants panel's own async fetch had a "Loading" heading and
"already granted" state that looked identical the instant a check ran too early, and per-connection
grants (unlike sessions/factors/API keys) are out of `clearMfaAndSessions`'s scope, so a second
suite run found the developer pre-granted from the first run's own setup — both fixed by waiting
for the grants fetch to settle before deciding whether to grant.

**Phase B5 (Semantic management) is complete** — proven end to end in a real browser (`web/next-app/
e2e/semantic.spec.ts`): a `developer` defines a metric through a structured expression builder
(aggregation + column dropdowns composing Section 8.3's `AGG([DISTINCT] column)` grammar, never a
free-SQL field — the Stitch mock's "Filter Clause" is omitted on purpose, since metric filters are
post-GA), moves it `draft` → `approved` → `deprecated`, and the next chat run demonstrably uses an
approved metric — proven not by a chart appearing (the scripted provider's generic fallback could
produce a similar-looking chart by coincidence) but by reading `dashboard.artifacts.validated_sql`
directly: it contains `COUNT(...)` and the metric's own name-derived alias, which the generic
fallback (hardcoded to `SUM`, aliased `"revenue"`) could never produce. Dimensions have no matching
Stitch screen, so they reuse the same screen as a second tab (Section 5.2's fallback rule). Zero
backend changes: semantic-service has been built and tested since Phase A7.

A real test-hygiene bug, not a product bug, cost a wasted debugging pass: a metric approved by one
suite run stays approved indefinitely (`clearMfaAndSessions` resets factors/sessions/API keys, not
semantic definitions), so a second run left two approved metrics sharing the same synonym, and the
scripted provider's lexical matcher resolved both — query-gateway then rejected the resulting
ambiguous plan (`QUERY_REJECTED`). Fixed by giving the spec its own fixture-reset step, the same
discipline `e2e/reset.ts` already applies elsewhere.

**Phase B6 (MCP governance) is complete** — proven end to end in a real browser (`web/next-app/
e2e/mcp.spec.ts`) against the real sample MCP server (`platform_testing.mcp`, :8765): a developer
registers a server with a declared tool manifest (name + `ToolClass` per tool), it shows
`pending_approval`; an org_admin approves it with step-up, after which its `read_metadata` tool is
grantable and invokable; the `write` tool is also grantable freely but genuinely refuses invoke
without a *fresh* step-up (Section 7.3's 5-minute window forced to have lapsed via direct DB
backdating of `identity.sessions.mfa_verified_at`, so the test exercises real re-authentication
rather than riding the still-fresh step-up from the same session's earlier TOTP enrollment); and
disabling the server makes its tools refused from the same still-open page. Zero backend changes
— mcp-gateway has been built and tested since Phase A9/A10.

Four real bugs found and fixed, all in the test, none in the product: (1) no MCP fixture reset
between suite runs meant a leftover `approved` server's status text substring-matched the
`getByRole("button", {name: "Approve"})` query on a later run — fixed with a `resetMcpFixtures()`
following the same discipline as B5's `resetSemanticFixtures()`; (2) the generic `div`-containing-
matching-`p`-text locator pattern matched every ancestor `div` up the tree, not just the specific
tool/server row — fixed with `data-testid="tool-row-{name}"` / `"server-row-{name}"`, the same
precedented fix B5 used for its own text-ambiguity case; (3) the initial test asserted a step-up
prompt always appears on a write-tool invoke, which is only true once the 5-minute freshness
window has actually lapsed — the first attempt rode the fresh step-up from the test's own earlier
TOTP enrollment and silently passed the wrong assertion path, so the DB-backdating fix above was
needed to make the test prove the real invariant instead of a coincidence of timing; (4) the
`beforeAll` reset only cleared `demo-admin`'s factors/sessions, not `demo-client`'s, even though the
second test signs in as `demo-client` and assumes a direct, ungated landing — a stray MFA factor
left on that account from unrelated manual testing put it behind a step-up challenge instead,
never reaching `/mcp` at all. Fixed by resetting both accounts, same as B1's `clearMfaAndSessions`
was always meant to be called.

**Phase B7 (Admin console) is complete** — proven end to end in a real browser (`web/next-app/
e2e/admin.spec.ts`): `(developer)/users`, `/policies`, `/audit`, `/billing` all reuse the existing
`(developer)` route group and its session/MFA guard rather than a separate `(admin)` group (Section
4.2's tree names one, but nothing in the actual build requires it — Next.js route groups never
appear in the URL, and B3–B6 already established this same shell as the shared "developer/admin
workspace"). An org_admin invites a user, grants/revokes another user's roles, force-revokes their
sessions, and resets their MFA — all real `user:manage`/`role:manage` + step-up calls against
Phase A10's already-tested endpoints. Self-targeting is refused unconditionally, a stronger
invariant than the DoD's literal wording ("refuses to demote/delete a tenant's last `org_admin`"):
`assert_not_self_target` (`roles.py`) blocks removing your own `org_admin` role or deleting your
own account regardless of whether you're the tenant's last admin, closing a whole class of
last-admin race conditions the DoD's count-based framing doesn't even need. Policies toggle and
persist across a reload; Billing renders only `GET /billing/usage`/`GET /billing/quotas` per
Section 5.2's caveat (no invoice/spend-cap UI, since `POST /billing/subscription` is a `501`
stub). Zero backend changes — every endpoint has been built and tested since Phase A10.

No demo Keycloak identity exists anywhere in this codebase for `auditor`/`billing_admin` (Track
A's own test suite only ever exercises those roles by constructing a `Principal` directly, never
through a real login) — standing one up would mean touching frozen Track A bootstrap infra for one
UI nuance. Instead, the DoD's "an auditor session shows read-only access" point reuses the suite's
own real role-grant action: it temporarily grants `demo-developer` the `auditor` role (proving the
UI's own grant path in the process), signs in as that session, and confirms Audit becomes reachable
and read-only while Users/Policies stay refused by permission, not by hiding a link — then reverts
the grant in `afterAll` so no other spec's `demo-developer` session is affected.

Two real bugs, both in the test: (1) the initial version assumed a step-up "Verify" prompt appears
before every mutating action, but Section 7.3's 5-minute freshness window means only the first
action after this test's own TOTP enrollment actually shows one — every action after that rides
the still-fresh window and succeeds directly, the exact wrong assumption already caught once in
B6, caught here before a run instead of after; (2) the last-org-admin assertions expected
`LAST_ORG_ADMIN`'s tenant-count message, but `change_roles`/`delete_user` both check
`assert_not_self_target` first, which refuses unconditionally with a different, more specific
message ("You cannot remove the organization administrator role from your own account." / "...
delete your own account.") — fixed by asserting the message the code actually produces.

**Phase B8 (Notifications, webhooks, guest share) is complete — Track B is now fully closed** —
proven end to end in a real browser (`web/next-app/e2e/b8.spec.ts`). A compact notification bell
lives in the shared `TopNav` for every authenticated role alike (no dedicated Stitch screen for
it — Phase A11's `GET /me/notifications` has no permission gate beyond being signed in), server-
fetched in both `(client)` and `(developer)` layouts and passed down, matching every other list in
this app rather than a client-side poll. `(developer)/webhooks` follows MCP Server Governance's
list/detail pattern (Section 5.2's explicit fallback rule): register (step-up), the signing secret
shown once and never again, disable (step-up) — gated on `user:manage` as a proxy for the real
server-side check, `require_org_admin`, a role check rather than a named Section 7.1 permission.
`(guest)/share/[token]` reuses B2's `ChartRenderer`/`parseChartSpec` directly against the
snapshot's already-inlined `chart_spec`/`data` (no per-tile artifact fetch, since a guest has no
session to fetch with) and deliberately has no `(guest)/layout.tsx`, so it gets zero chrome beyond
the root `<html>/<body>`. Zero backend changes -- notification-service and dashboard-service's
share-link/snapshot surface have both been built and tested since Phase A11.

A real gap, caught by reasoning about the guest flow before ever loading it, not by a failing
test: `proxy.ts`'s `PUBLIC_PATHS` never included `/share`, so an unauthenticated visitor would have
been bounced to `/login` before the guest page ever rendered — fixed by adding it alongside
`/login`/`/callback`/`/invitations`.

Two real test-hygiene bugs, both the same class already seen in B5-B7 and both caught by direct
DB inspection before touching any code: (1) the dashboard-pin notification `provision_demo_
dashboard.py` produces is a fixed row from an idempotent pin, not something a re-run regenerates —
a second suite run found it already `read_at`-set from the first run's own mark-as-read, so the
"is it unread" assertion failed on a real notification with no product bug behind it; fixed with a
`resetPinNotification()` step. (2) `SharePanel`'s table lists a dashboard's entire link history,
active or revoked -- a second run's `getByText("Revoked")` matched both the new run's freshly-
revoked link and the previous run's leftover row, a strict-mode violation. What looked at first
like a deeper bug (a revoked token's guest page still rendering after a second full-suite run) was
chased all the way down to a raw curl-equivalent proof that the backend was correct, before the
real cause turned out to be this same accumulation -- worth recording as a reminder that a
convincing-looking caching mystery is still worth ruling out the mundane fixture-hygiene
explanation on first. Fixed by giving the spec its own `resetShareLinks()`.

Next up: Track C, Phase C1 (Production hardening). See Section 31 of the build spec for the full
B1–B8 list, and this file's "Carried forward" list above for every item C1 requires before it can
start.**

**Frontend design reference (read before writing any Track B page):** Section 5.2 of the build
spec names, screen by screen, which of the 15 Stitch screens (project `10440972999306255957`,
"BuVi Enterprise BI Platform," reachable via the `stitch` MCP server) each route builds against,
and which existing screen's pattern to follow for the handful of routes with no matching screen.
Section 5.1's palette/token values are unchanged — the Stitch design system already agrees with
them; do not restyle a screen away from what Stitch shows, and do not invent shadows, gradients,
rounded-pill badges, or emoji anywhere the Stitch set doesn't have them.

**Carried forward (do not drop):**
- **B8 note:** `/account`'s API key create flow (`ApiKeyPanel`, show-once secret display, inline
  step-up via `MfaVerifyForm`) is the pattern to reuse for the webhook signing secret in B8 —
  same shape, same "shown once, never again" rule (Section 6.8/9).
- **Phase C1 candidate (ADR 0020):** SQL Lab's "Send to Chat/Chart" (B4) reseeds the chat composer
  and lets the full pipeline regenerate SQL from scratch, rather than skipping straight to
  execution with the already-validated query. A true skip-regeneration entry point needs a CrewAI
  `@router()` branch, a synthesized `SchemaContext`/`AnalyticsRequest` for the stages it bypasses
  (`_authorize_query`'s real Section 7.2 check trusts `schema_context`, so this touches Track A
  security surface, not just UX), and a decision on what its SSE events look like against Section
  32's hardcoded 9-stage sequence — investigated directly against `run_executor.py` and judged
  non-trivial for a Track B phase; do not build it without going through the same design/test
  rigor as any other change to query authorization.
- **Before Phase C1 starts (required):** Keycloak's realm and the identity Postgres schema must
  be backed up and restored as one consistency domain, never independently (ADR 0019). Found
  live: a Docker Desktop restart brought Postgres back with its volume intact but Keycloak
  without one, leaving `identity.users.idp_subject` pointing at Keycloak subjects that no longer
  existed — every affected user was refused login with `403 USER_NOT_ACTIVE` ("not provisioned
  for any organization"), a message that reads like a normal authorization decision, not a
  restore-consistency symptom. C1's DR restore drill must restore both from the same point and
  prove a real login for a pre-existing user succeeds afterward, not just that both services
  start.
- **Running the browser E2E suite:** it needs `make up`, identity-service, api-gateway and
  `npm run dev`, and api-gateway must be started with a raised auth-tier limit
  (`GATEWAY_RATE_AUTH_IP_CAPACITY=200 GATEWAY_RATE_AUTH_IP_REFILL_PER_SECOND=20`) — the exact
  commands are in `web/next-app/playwright.config.ts`'s header. The default limit is tuned for a
  human at a login form; a dozen-plus real OIDC round trips queue behind it for minutes. The
  limiter keeps its production defaults everywhere else and keeps its own coverage in
  `scripts/test_login.py`'s burst check. Each spec resets only its own demo user's factors,
  sessions and `mfa_verification_failed` audit rows (those rows are the 15-minute per-account
  lockout state), so specs are order-independent and the suite is idempotent.
- **Production deployment requirement (ADR 0018):** `IDENTITY_OIDC_FRONTEND_REDIRECT_URI` must be
  set to the deployed Next.js app's own `/callback` URL, and that same URL registered with the
  IdP — dev's defaults (`localhost:3000/callback`, already registered in
  `scripts/keycloak-bootstrap.sh`'s wildcard) do not carry over automatically.
- **Track B note:** the Stitch "Usage & Quotas" screen shows invoice-reconciliation and a
  spend-cap control that nothing in the backend implements (`POST /billing/subscription` is a
  `501` stub — see the post-GA line below). Phase B7 renders only the parts backed by
  `GET /billing/usage` / `GET /billing/quotas`; do not wire the rest to a live action (ADR 0017).
- **Track B note (resolved):** `(developer)/data` shipped in B3 with its own dedicated coverage
  (`e2e/data-sources.spec.ts`, a unique `e2e-source-*` connection per run). `chat-and-dashboards.
  spec.ts` keeps using `scripts/provision_demo_data_source.py` for its own precondition rather than
  switching to the UI -- a chat test driving B3's UI just to get a data source would blur which
  feature actually broke on a failure, the opposite of what B1-B3's per-spec-resets-its-own-state
  discipline is for.
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
2. **Track B (Phases B1–B8)** — the Next.js frontend, built only after Track A's exit gate
   (Phase A12) passes: a full automated backend test suite, green in CI, with an OpenAPI-
   generated TypeScript client committed to `packages/ts/api-client`. Each phase builds against a
   named screen from the Stitch reference set (spec Section 5.2) — read that before starting one.
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
