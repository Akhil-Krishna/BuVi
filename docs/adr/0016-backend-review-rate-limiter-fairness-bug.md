# 0016 — Post-A12 backend review: rate-limiter fairness bug

- **Status:** Accepted · **Date:** 2026-09-20 · **Phase:** between A12 and B1 (backend frozen except bug fixes, per CLAUDE.md's build order)
- **Related:** Section 20, 24; [ADR 0003](0003-phase-a2-api-gateway.md) (the gateway's rate-limit tiers); `tests/system/section_24.py`.

## Trigger

A requested full-backend review ("identify all bugs, fix them, confirm the backend is in optimum condition") rather than a specific failure report. Followed the `backend-debugging` skill's playbook: full static gate (lint, typecheck, every service's unit/integration suite), `gitleaks` (installed locally to match CI), `make backend-e2e` (all 13 steps), then manual review of concurrency-sensitive and less-recently-touched code.

## What was found

**`RedisRateLimiter.consume()` permanently spent a bucket's token on a request that was ultimately denied by a *different*, later bucket in the same call** (`apps/api-gateway/src/api_gateway/infrastructure/cache/rate_limiter.py`).

`policy.after_auth()` passes `[user_bucket, tenant_bucket]` to one `consume()` call. The method looped the buckets in order, calling the atomic take-a-token Lua script for each; on the first bucket that returned "empty," it returned `Decision(allowed=False, ...)` immediately — but any *earlier* bucket in the same list that had already returned "allowed" had already had its token deducted, with nothing to give it back.

Concretely: when a tenant's shared bucket was exhausted (its intended job — "one noisy tenant cannot starve the platform"), *every other user in that tenant* had one of their own per-user tokens silently and permanently burned on each request denied this way, even though those users personally did nothing wrong. A well-behaved user sharing a tenant with a noisy one would see their own rate-limit budget quietly drained by denials that were never their fault.

This directly undermines Section 24's requirement — verified by an existing but insufficiently pointed test (`test_tenant_bucket_caps_all_users_of_a_tenant`), which used a default `rate_user_capacity` of 120 while tightening only the tenant bucket to 3, so the one wrongly-spent token per denial was invisible against a 120-token budget. The bug had no reproduction until a test tightened *both* bucket capacities.

## Fix

`consume()` now tracks which buckets it has already taken a token from in the current call (`taken: list[Bucket]`). On denial by a later bucket, it refunds every already-taken bucket via a companion Lua script (`TOKEN_BUCKET_REFUND_LUA`) before returning the denial — clamped to the bucket's own capacity, so a refund can never push a bucket over its ceiling even under concurrent activity. The refund is best-effort (a `RedisError` during refund is logged and otherwise ignored): losing a refund costs only a little of that bucket's own future budget, the same trade-off already accepted for a lost concurrency-limiter lease elsewhere in the codebase — it never turns into an incorrect *allow*.

The gateway's other multi-step rate check (`before_auth`'s single IP-tier bucket, spent before identity-service introspection even runs) was checked and is *not* the same bug: it is a deliberately separate, single-bucket call, and its token is meant to be spent once the IP has caused an introspection call regardless of what authorization decides afterward (documented in the module's own docstring). No other sequential-resource-claim-without-rollback pattern was found elsewhere in the codebase (mcp-gateway's invocation path, the Redis tenant-concurrency lease, and the JetStream consumers were checked and are linear check-then-raise or independent per-message loops, not sequential budget spends).

## Verification

- New regression test: `apps/api-gateway/src/api_gateway/tests/integration/test_gateway.py::test_a_request_denied_by_the_tenant_bucket_does_not_spend_the_users_own_budget`. Confirmed it fails against the pre-fix code (observed `2.0000009` remaining tokens instead of the expected `3` — proof the bug was real, not a fixture artifact) and passes after the fix.
- Registered in `tests/system/section_24.py` under "`sql:execute` and MCP invocation both have per-tenant concurrency," alongside the existing tenant/user bucket tests it strengthens.
- Full suite after the fix, all green: 1,784 tests across every service and shared package (`api-gateway` now 301, +1 for the new test), `ruff check`/`format --check`, `mypy` (all service layers), `scripts/diff-contracts.sh` (no drift), `gitleaks detect` (installed locally for this review; 0 leaks, 75 commits scanned — the only findings were Next.js's own gitignored `.next/` build cache, never tracked), and `make backend-e2e` (all 13 steps: contracts, every live DoD flow A1–A12, the generated-client flow, and the live `system` suite).

## Scope note

This was a full-backend review, not a new phase. No other genuine bugs were found: the exception-handling audit (every `except Exception` in every service), datetime timezone usage, f-string SQL construction, pagination cursor logic, connection lifecycle in the Postgres/MySQL connectors, the SSE bridge's replay/resync logic, and the ChartSpec encoding-type validation were all read and are either already covered by existing tests or are consistent, intentional design (the last of these — quantitative fields being disallowed from nominal/ordinal/color encodings — has no test or spec text pinning down an alternative, so it was left as-is rather than guessed at).
