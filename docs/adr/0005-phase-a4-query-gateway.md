# 0005 — Phase A4: query-gateway decisions

- **Status:** Accepted · **Date:** 2026-09-15 · **Phase:** A4

## Decisions

1. **One route, two credentials.** `POST /internal/v1/queries` (Section 13's body) is the only route; no
   public `/sql/*` route is wired in A4. A request needs the calling service's JWT with
   `query-gateway:execute` *and* the end user's forwarded session or API key, re-authenticated through
   identity-service. A user is never taken from a header or body field (unknown body fields are refused).

2. **Purpose is bound to the caller and to a permission.** `sql_editor` → caller `api-gateway`, user
   `sql:execute`. `analytics_run` → caller `analytics-orchestrator`, user `chat:use` (the orchestrator
   runs queries for chat users, who do not hold `sql:execute`). `export` → `422 PURPOSE_NOT_SUPPORTED`
   until an export phase defines its thresholds (Section 7.3). Mapping is configuration
   (`QUERY_GATEWAY_PURPOSE_CALLERS`).

3. **Connection policy comes from metadata-service** via the new internal
   `GET /internal/v1/data-sources/{id}/query-policy?tenant_id=` (scope `metadata-service:query-policy`,
   registered for the `query-gateway` client). The tenant is the authenticated principal's, and
   metadata-service answers 404 for any other tenant's data source. Cached 30 s per (tenant, source).
   query-gateway reads the credential from Vault itself (Section 13: the only holder), and only follows a
   `secret_ref` equal to that tenant's own `…/tenants/<tenant>/datasources/<id>` path.

4. **Validator** (`domain/policies/sql_validator.py`, sqlglot, Postgres dialect only):
   - one statement; root must be `SELECT`/set operation;
   - no write or command node anywhere in the tree, including data-modifying CTEs;
   - no `INTO`, row locks, bind parameters, schema-qualified function calls, or `reg*` casts;
   - a **function allow-list** (parsed class or known Postgres name);
   - every table must resolve against the catalog in `allowed_schemas` (unqualified names must be
     unique); unknown columns are rejected via qualification;
   - agent path also rejects `is_pii` columns (including through `*` and CTEs) and
     `is_visible_to_agent = false` tables.

   Reject on any parse or analysis failure. **What executes is the SQL regenerated from the validated
   tree**, fully qualified with comments stripped, and re-checked. The caller's text never runs. Errors
   are `422 QUERY_VALIDATION_FAILED` with `details.reason` and at most the offending keyword or
   identifier.
   **For review:** Section 12's PII exclusion is written for agent context; the developer SQL editor
   path currently may read PII columns of catalogued tables. Tighten if the spec owner wants `pii:read`
   there too.

5. **Database-side defense in depth** (independent of the validator): one prepared statement (the
   protocol refuses multiple commands); `default_transaction_read_only=on` plus a read-only REPEATABLE
   READ transaction per query; empty `search_path`; per-transaction `statement_timeout` plus a client
   timeout; a server-side cursor with row and byte caps that always flags truncation; Section 15 egress
   checks on pool creation (via `platform-egress`); pools keyed by data source and credential
   fingerprint, dropped on connection/auth failure. Tests run DELETE/INSERT/CREATE/multi-statement
   straight at the executor to prove the database refuses them.

6. **Result handles.** The capped result is stored as JSON at `tenants/<tenant>/queries/<query_id>.json`
   in the `query-results` bucket, whose lifecycle rule expires objects after `result_ttl_days` (default
   1). S3 lifecycle has day granularity, so a result lives ≥ 24 h and < 48 h. The in-memory store is
   refused outside dev/test.

7. **Audit.** One `query_executions` row per request, written at the end: `succeeded`, `rejected`,
   `failed` or `timeout` (`validated`/`running` stay unused until execution becomes asynchronous).
   Rejections are recorded: they are the security signal. `sql_text` is the caller's text (Section 8.5);
   `sql_hash` is of the executed SQL when valid. UPDATE/DELETE/TRUNCATE are revoked from `buvi_app`;
   RLS as everywhere else.

8. **Per-tenant concurrency** (Section 20) is enforced per process, refusing with `429` rather than
   queuing. A cluster-wide cap (shared leases) is a Track C item.

9. **`platform-egress`** now holds the Section 15 policy and resolver (ADR 0004 item 8); metadata-service
   imports it unchanged in behaviour.

## Gaps — not implemented, need a decision

- **Per-connection grants.** Section 7.1 ("`sql:execute` (per-connection grant)") and Section 13
  ("authorize database access (tenant + per-connection grant)") have no storage in Section 8. Today a
  user with `sql:execute` may query any active data source of their tenant. Proposal: a grants table
  owned by metadata-service (managed with `data:manage`, checked through the query-policy endpoint),
  added to Section 8.2 by the spec owner.
- **Row/column tenant filters for multi-tenant customer schemas** ("mandatory WHERE tenant filters",
  Section 13) need per-source policy storage that does not exist yet.
- **`EXPLAIN` cost estimation** ("optionally") is not implemented.
- **`query.completed` event** (Section 18.1) is not published: no broker is wired yet.
- **`/sql/validate`, `/sql/execute`, `/sql/history`** stay `501` at the gateway: Phase A4 says "no public
  route yet" and no later phase names when they are wired.
- `Idempotency-Key`: Phase A5 (ADR 0004).
