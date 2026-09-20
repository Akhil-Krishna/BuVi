# 0015 — Phase A12: backend completeness gate

- **Status:** Accepted · **Date:** 2026-09-20 · **Phase:** A12
- **Related:** spec commit c0f77de (replan); Sections 9, 19, 24, 25, 26, 31 (A12, C1); [ADR 0014](0014-phase-a11-notifications-webhooks-usage.md) (the open A5 stall).

## Plan issues found before implementation (fixed in the spec)

| Issue | Resolution |
|---|---|
| "A single automated suite": eleven per-phase live flows already existed, but CI never ran any of them | `scripts/backend-e2e.sh` chains contracts, every flow and the live Section 24 suite. A new CI job runs it against the compose stack on the runner. The Makefile's `test-live` list stays the only list of flows. |
| "Run the full Section 24 checklist … (all of them are [testable])": not true of the current code. Image signing, digest pinning, OIDC federation and image scanning are deployment controls; the tenant-deletion cascade and per-tenant MCP limits are not built | A registry maps each bullet to its enforcing tests, or to an explicit **Phase C1** deferral. A test fails on an uncovered bullet, a missing test, or a deferral that Phase C1 does not list. |
| "`gen-client.sh` produces a working client": which contract, and what "working" means were undefined | Generated from `api-gateway.json` (the only browser surface) with `openapi-typescript` and `openapi-fetch`, committed, drift-checked in CI, and driven against the running gateway (`test-client`). |
| The intermittent A5 crash-resume stall (ADR 0014) would make a per-push gate flaky | Handled first (below). |

## Decisions

1. **One suite, three layers.**
   - Static controls and contracts run in every CI build (`pytest tests`).
   - Live flows and `system` tests run in the `backend-e2e` job.
   - The `system` tests are skipped without `BUVI_SYSTEM=1`. With it set, an unreachable stack is a failure, never a skip.
2. **The Section 24 registry** (`tests/system/section_24.py`) is code, not a document, so it cannot rot silently.
   - New controls tested against the live stack:
     - every service table has RLS enabled **and forced**;
     - `buvi_app` is not a superuser, lacks BYPASSRLS and owns no tables;
     - `buvi_reader` holds only SELECT (Postgres) or SELECT and USAGE (MySQL);
     - the result bucket has an expiry rule of 7 days or less;
     - Keycloak's realm locks an account after at most 5 password failures.
   - New static controls:
     - no service's code or migrations reference another service's schema;
     - no `eval`/`exec`/`compile`/`subprocess`/`pickle` in any service;
     - every image runs non-root with a `HEALTHCHECK`;
     - CI runs pip-audit and npm audit;
     - CI holds no cloud credentials.
   - Deferred to Phase C1 (and listed in its spec text):
     - image scanning, digest pinning, cosign signing and OIDC federation;
     - per-tenant MCP invocation limits;
     - the tenant-deletion cascade.
3. **The client.**
   - It sends credentials: the session is an HttpOnly cookie, and the client never holds a token.
   - It adds an `Idempotency-Key` to every mutation, so retries are safe by default.
   - It exposes typed helpers for the error envelope and `STEP_UP_REQUIRED`.
   - Track B imports it as `@buvi/api-client`.

## Found while building and reviewing (fixed)

- **The gateway's contract dropped every query parameter.**
  - Its OpenAPI composition borrowed request and response bodies from the owning services, but not their query parameters.
  - So filters and pagination were untyped for 12 routes: `/me/notifications`, `/billing/usage`, every `limit`/`cursor` list, and more.
  - The client's type-check caught it. Query parameters are now composed in (path parameters stay the gateway's), with a contract test that fails if any is missing.
- **Section 24 "per-account" limits on MFA verify did not exist.**
  - Only the gateway's per-IP bucket limited TOTP guesses, so a session holder could spread guesses across addresses.
  - identity-service now refuses verification (429 `MFA_TOO_MANY_ATTEMPTS`, `Retry-After`) after 5 failures in 15 minutes.
  - The failures are counted from the durable `auth.mfa_verification_failed` audit events, so the limit holds across replicas and restarts.
  - Password guessing was already limited per account by Keycloak's brute-force protection; that is now verified live.

## The A5 crash-resume stall (ADR 0014 "Open")

- **Status: a Phase C1 entry requirement** (spec d54d322); ADR 0014 records why the trigger is dev-only while the failure class is not, and what production must set.
- **Result:** 8 further looped runs with a lock-wait watcher polling `pg_stat_activity` every 2 s gave 8 passes, 0 lock waits and 0 stuck transactions. That is 14 passes and 1 failure in total. It does not reproduce on demand, so it is **not** declared fixed.
- **So that a recurrence explains itself and cannot hang long:**
  - a stage timeout now logs `stage timed out` with the innermost frames it was awaiting (file, line and function only);
  - the dev/CI Postgres runs with `log_lock_waits=on`, `deadlock_timeout=1s` and `idle_in_transaction_session_timeout=60s`. A client that died mid-transaction (the leading suspect: Docker Desktop's port proxy delaying EOF) releases its locks within a minute, and the wait is logged with its blocker;
  - CI keeps every flow's service logs as an artifact.

## Verification

- **Local `make backend-e2e`:** every live flow (A1-A11 and `test-client`) and the `system` suite passed. The run found one bug in the script itself: the log directory was created only for flows, so the first step could not write its log. Fixed, and the contracts step re-run green (47).
- **Suites:**

  | Suite | Tests |
  |---|---|
  | repo (`tests`, including the Section 24 registry and static controls) | 99 (live controls skipped) |
  | packages | 128 |
  | api-gateway | 300 |
  | identity-service | 256 |
  | query-gateway | 546 |
  | metadata-service | 174 |
  | analytics-orchestrator | 86 |
  | mcp-gateway | 61 |
  | semantic-service | 34 |
  | dashboard-service | 30 |
  | visualization-service | 27 |
  | notification-service | 23 |
  | worker-runtime | 19 |

- **Contracts and client:** every OpenAPI document validates; the contract diff reports no breaking changes; `gen-client.sh --check` passes.
- **CI:** all 18 jobs green, including `backend e2e (Track A exit gate)` and `api-client`, on the commit that follows the fixes below.
- **Found only in CI:**
  - A1's invitation single-use check saw `429` instead of `400`. The runner's faster logins spent the accept route's auth-tier bucket (10 per IP) within one burst. The check now honours `Retry-After` once, as a real client would; the rate-limit checks still call directly. Found through the new failure annotations, since the job logs need repo rights and annotations do not.
  - `npm audit` failed in two jobs. npm 10 calls a retired endpoint that now answers `400`, and npm's bulk advisory endpoint was briefly down for maintenance. The audits now run with npm 11 (bulk advisories) and report 0 vulnerabilities.
