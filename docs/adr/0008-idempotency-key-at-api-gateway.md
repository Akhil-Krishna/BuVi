# 0008 — Idempotency-Key at api-gateway

- **Status:** Accepted · **Date:** 2026-09-17 · **Before phase:** A7
- **Context:** §9 has always said every mutating endpoint accepts `Idempotency-Key`. Until now only `POST /conversations/{id}/messages` did (ADR 0006), and the gap resurfaced in ADRs 0004, 0006 and 0007. Semantics are in spec §9 (commit b4afdde).

## Decisions

1. **One implementation, at the edge.** `application/services/idempotency.py` runs inside every catalog route, *after* `authorize`, so authentication, permission, step-up and rate limits apply to every request, including a replay. Records live in Redis (`infrastructure/cache/idempotency_store.py`).

2. **Scope and fingerprint.** The requested design keyed records by tenant + principal + route + key. We deliberately changed that:
   - **Record key:** `idem:{tenant}:{principal}:{sha256(key)}`.
   - **Fingerprint:** method, path, query and body bytes, length-prefixed.
   - **Why:** with the route in the record key, the same key sent to two routes would silently be two operations. With the route in the fingerprint, that reuse is detected: `409 IDEMPOTENCY_KEY_REUSED`. This matches Stripe and the IETF `Idempotency-Key` draft. The key is hashed, so no client text lands in Redis key names.
   - **Status code:** the draft suggests `422` for reuse. We use `409` to match the orchestrator's existing `IDEMPOTENCY_KEY_REUSED` contract.

3. **Lifecycle.**
   - An atomic Lua `begin` writes a `pending` record whose lock TTL is the upstream timeout plus 30 s. A concurrent duplicate gets `409 IDEMPOTENCY_REQUEST_IN_PROGRESS` with `Retry-After`.
   - A final response completes the record for 24 h. Final means 2xx, or a 4xx other than 401, 403, 408, 409, 425 or 429.
   - A retryable outcome, or an exception such as an upstream outage, releases the record. Release is compare-and-delete on a per-request token, so a late release never removes someone else's record.

4. **Three modes per route** (`RouteSpec.idempotency`):
   - `replay` (default): the stored status, content type and body are replayed with `Idempotent-Replayed: true`.
   - `no_store`: completion is recorded without the body, and a retry gets `409 IDEMPOTENT_REPLAY_UNAVAILABLE`. It applies to:
     - responses carrying a one-time secret: `/auth/mfa/enroll`, `/me/api-keys`, `/admin/webhooks`, share links;
     - responses carrying customer rows: `/sql/execute`, MCP tool invocation. Storing those would put customer data at rest outside §13's TTL store.
   - Any response that sets a cookie or exceeds 256 KB is also stored without its body.
   - `ignore`: public routes (no principal to scope to), plus `/auth/logout` and `/auth/mfa/verify` (session state).

5. **Store outage fails closed, for keyed requests only.** A request carrying a key gets `503 IDEMPOTENCY_UNAVAILABLE` rather than being run without the guarantee it asked for. Requests without a key are unaffected. Rate limiting still fails open (ADR 0003).

6. **Defense in depth, not a replacement.** This is a retry guard, not durable exactly-once. If the gateway dies after the backend commits but before the record completes, the lock expires and a retry can run again. Operations whose duplicates are costly keep durable dedupe in their owning service: run creation keeps its unique `(tenant_id, idempotency_key)` constraint (ADR 0006), and future billing and MCP write operations should follow suit.

## Consequences

- Closes the Idempotency-Key gap recorded in ADRs 0004, 0006 and 0007 for every current and future §9 mutating route: a new catalog row gets `replay` unless it opts out.
- Clients can safely retry `POST /dashboards`, `POST /dashboards/{id}/tiles`, `PATCH /tiles/{id}`, data-source writes and so on.
- Tests: `tests/unit/test_idempotency_policy.py` checks the rules and each route's mode; `tests/integration/test_idempotency.py` checks, against real Redis, replay, reuse, principal scope, authorization before replay, retryable outcomes, concurrency, no-store routes, ignored routes and the store outage. The live `make test-dashboards` flow checks replay and reuse through the real stack.
