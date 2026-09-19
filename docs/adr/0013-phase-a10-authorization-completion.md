# 0013 — Phase A10: step-up by method, WebAuthn, tenant policies, sharing, quotas, public SQL

- **Status:** Accepted · **Date:** 2026-09-19 · **Phase:** A10
- **Related:** spec commits 9ad09bd (replan) and 76ac62e (share-link revocation); Sections 2, 3, 6.6, 7.1, 7.3, 8.1, 8.2, 8.6, 9, 20, 23; [ADR 0002](0002-phase-a1-identity-decisions.md) (step-up, deferred MFA reset), [ADR 0012](0012-phase-a9-mcp-gateway.md) (write tools deferred here).

## Plan issues found before implementation (fixed in the spec)

| Issue | Resolution |
|---|---|
| "Implement every admin endpoint" and "implement step-up middleware" were mostly done: A1 built users, roles, invitations, session revocation, deletion and audit, plus `require_step_up` and the gateway's step-up flag | A10 states what remains: step-up judged **by method**, and applied to the Section 7.3 operations that lacked it; WebAuthn; MFA reset; policies. |
| `/admin/webhooks` needs notification-service, which A11 scaffolds | Moved to A11. |
| "Connection approval" ("approval by admin for prod") has no basis in the DDL: data sources have no environment attribute | Replaced by what Section 7.1 actually needs: **per-connection `sql:execute` grants** (`metadata.data_source_grants`) decided by `org_admin`. |
| The public `/sql/*` API was scheduled in no phase (ADR 0005), though Phase B3 builds its UI against it | Built in A10, with the per-connection grants, which are its authorization. |
| "Tenant sharing policy" (7.1) and developers' "`mcp:manage` (if granted)" had nowhere to live | Tenant policies in identity-service (Section 3: "policy mapping"), applied tenant-wide. Per-user permission lists would be a second authorization model to audit and revoke. |
| "Query concurrency limits" existed only per process: N replicas meant N times the cap | Cluster-wide Redis leases (below). |
| A quota breach reached a run as `UPSTREAM_UNAVAILABLE`: query-gateway's 429 was unmapped | A bounded retry, then the documented `QUERY_CONCURRENCY_LIMITED`. |
| Four-eyes semantic approval (ADR 0010 said "a tenant policy for Phase A10") is not in A10's text | Post-GA backlog. It needs the policy in semantic-service's authorization path. |

## Decisions

1. **Step-up is judged by method.**
   - Sessions record `mfa_verified_method`. `Principal` carries `mfa_method` and `webauthn_required`; identity-service sets the latter from the caller's role and the tenant policy.
   - `Principal.step_up_is_fresh()` refuses the wrong method. `platform_super_admin` always needs WebAuthn, even if a payload omitted the flag.
   - The gateway and every service (`require_step_up`) therefore enforce identically.
   - One shared refusal: `StepUpRequiredError` (platform-auth), `403 STEP_UP_REQUIRED` with `details.method` (`webauthn`/`any`) and `WWW-Authenticate`. Services used to answer a generic `403 FORBIDDEN`. `GET /auth/session` exposes `step_up_method`.
2. **Section 7.3 coverage.**
   - New gateway step-up routes:
     - `POST /me/api-keys` (creating or rotating a key);
     - `DELETE /me/mfa/{id}`;
     - `POST /admin/users/{id}/mfa/reset`;
     - `PATCH /admin/policies`.
   - Step-up that depends on data only the owning service sees is enforced there:
     - write/admin MCP tool grants and invocations;
     - result reads above the export threshold (`/sql/execute` with `max_rows` over 10,000; `/artifacts/{id}/data` returning more);
     - enrolling a second MFA factor (Section 6.6).
   - A catalog test pins the 7.3 route list, so a step-up flag cannot quietly disappear.
3. **WebAuthn** (identity-service, py_webauthn 3.0).
   - `none` attestation, ES256/EdDSA/RS256 keys, user verification `preferred`.
   - Credential id, public key and signature counter live in Vault; the row holds `secret_ref` (Section 8.1).
   - One pending challenge per session, stored on the session row with its ceremony (`reg:` or `auth:`), taken and cleared in one step, and expiring after 120 s.
   - `POST /auth/mfa/challenge` sits on the ordinary per-user rate tier, not the per-IP `auth` tier. It checks no secret, so there is nothing to guess, and a WebAuthn step-up then spends one auth-tier token (the verify), not two.
   - Tested end to end with a software authenticator (`platform_testing.webauthn`) against the unmodified library: a wrong origin, a cloned key (counter regression), an unknown key, a replayed assertion and a crossed ceremony are all refused.
4. **Tenant policies.** `identity.tenant_policies` (a missing row means the most restrictive defaults):
   - `client_can_share_dashboards`;
   - `developer_can_manage_mcp`;
   - `org_admin_requires_webauthn`.

   One function computes effective permissions for sessions, API-key owners and delegated resolution. A change applies on the next request and is audited with the state before and after.
5. **MFA lifecycle.**
   - Only the first factor is free; every later factor needs a fresh step-up.
   - Removing your own factor needs step-up.
   - An admin reset (another user only, step-up) revokes every factor, deletes their Vault secrets and ends the user's sessions.
   - **Adding a WebAuthn key accepts any fresh factor**, and **the WebAuthn policy cannot be enabled by an admin who has no key.** Both close lockouts found while designing the live flow (below).
6. **Share links** (dashboard-service).
   - Creating a link: the owner with `dashboard:share` and a fresh step-up.
   - Tokens: 256-bit (`token_urlsafe(32)`), stored as SHA-256, shown once, capped at 168 h, at most 20 active per dashboard.
   - **Revoking never needs more than creating did:** the owner, or any `org_admin` (for a leaked link), with `dashboard:read`.
   - `GET /share/{token}`:
     - public and rate-limited;
     - the token is looked up through a FOR SELECT policy switched on for one transaction (as for identity's pre-authentication lookups);
     - the reads that follow are bound to the link's tenant;
     - it returns names, chart specs, positions and data only, with `no-store`, `no-referrer` and `noindex`;
     - tile data is omitted when it has expired or is export-sized.
7. **MCP.**
   - `disable` (approved → disabled) needs no step-up: removing access must never wait on MFA.
   - `reject` (pending → rejected) is final.
   - Re-enabling means re-approving, with step-up and re-verification.
   - Write/admin tools are `require_grant`; grants and every invocation need a fresh step-up, and admin tools need `org_admin`.
   - Migration 0002 moves existing write/admin rows from `deny`. No grant is widened, because none could exist.
8. **Quotas.**
   - Query concurrency: a per-tenant Redis sorted set of leases, managed by one Lua script on Redis's clock.
     - The lease outlasts the longest query plus 60 s, so a dead replica frees its slots.
     - A Redis outage falls back to the per-process cap, never to none.
     - Tested against real Redis: two limiters share the cap, a crashed holder's lease expires, and an outage falls back.
   - Runs retry a 429 three times with exponential backoff, then fail `QUERY_CONCURRENCY_LIMITED`.
   - `GET /billing/quotas` (analytics-orchestrator, `billing:read`) reads the ledger the ModelRouter enforces.
9. **Public SQL API** (query-gateway, through api-gateway with `query-gateway:proxy`).
   - It uses purpose `sql_editor`, with the same validator, executor, audit and caps as the chat flow.
   - The per-connection grant is checked on every call through an **uncached** metadata endpoint (the 30 s policy cache would delay a revocation).
   - Responses never include the result handle.
   - `GET /sql/history` shows the caller's own queries. With `run:debug` it shows the whole tenant's (Section 9), and every role with `sql:execute` also holds `run:debug` (7.1).

## Found while building and reviewing (fixed before commit)

- **Failed MFA checks were never audited** (a Phase A1 bug). The failure event was written in the refused request's transaction and rolled back with it. Login refusals had the same flaw.
  - Refusal events and the spending of a WebAuthn challenge now go through `IndependentWrites`: their own short transaction on their own connection.
  - Committing the request's session mid-request was not an option: identity binds its tenant with a session-level setting, so a mid-request commit would return a pooled connection still bound to the tenant.
  - Regression tests cover both.
- **A failed WebAuthn attempt left its challenge reusable** (the same rollback). It is now spent either way.
- **Lockout from the WebAuthn policy:**
  - an admin with only TOTP could never enroll the key the policy now demanded;
  - the enabling admin could lock themselves out of every step-up operation, including disabling the policy.
  - Both are fixed, with tests.
- **Share-link revocation required `dashboard:share`**, so a client whose sharing was withdrawn could not revoke live links, and admins could not revoke leaked ones. The spec was fixed first (76ac62e); regression tests added.
- **`rollback()` expired the snapshot's loaded rows** (DetachedInstanceError). The explicit rollbacks were removed; closing the session ends the read-only transactions.
- **Stale tests:** A4's "no public route", A9's "write tools are never grantable", and services expecting `FORBIDDEN` instead of `STEP_UP_REQUIRED` were updated to the new contract.
- **A mistake of my own during the work:** a `git stash` of one file, restored at once and verified by the full identity suite.

## Verification

- **Unit and integration suites**, all green:

  | Suite | Passed | Covers |
  |---|---|---|
  | identity-service | 245 | WebAuthn ceremonies and failure modes, step-up by method, policies and their effect on every principal, MFA removal and reset, role catalog, audit of failures |
  | api-gateway | 292 | Section 7.3 route coverage; Section 9 parity |
  | dashboard-service | 29 | share links, guest snapshot, revocation, export threshold |
  | mcp-gateway | 104 | write/admin tools, disable/reject/re-approve |
  | metadata-service | 174 | SQL grants and the uncached check |
  | query-gateway | 543 | public SQL API, grants, export threshold, history, real-Redis concurrency |
  | analytics-orchestrator | 83 | 429 retry, `QUERY_CONCURRENCY_LIMITED`, `/billing/quotas` |
  | repo and shared packages | 124 | platform-auth's step-up rules included |

- **Live** (`make test-admin`, through api-gateway on the real stack):
  - Eight Section 7.3 operations, each refused without step-up (`403 STEP_UP_REQUIRED`) and accepted with it: credentials, invitation, role change, force logout, MFA reset, API key, tenant policy, share link.
  - `/sql/execute` above the export threshold: refused when stale, allowed when fresh.
  - A developer's SQL: without a grant, then with one, then after revocation.
  - A guest snapshot before and after revocation.
  - A WebAuthn key registered and used for step-up; the TOTP refusal under the policy.
  - Quotas readable; every change audited.

## Gaps

- User deletion and the MCP write-tool paths are proven in their services' suites, not in the live flow: deletion would break demo users other flows rely on, and the MCP paths need the sample MCP server.
- A share link outlives its creator's deactivation until it expires or an admin revokes it: identity does not notify dashboard-service. It is noted in the runbook; a user-deactivated event is an A11/C1 candidate.
- `/billing/quotas` reports LLM tokens. Query concurrency is enforced with a documented error but not reported. Usage aggregation is A11.
