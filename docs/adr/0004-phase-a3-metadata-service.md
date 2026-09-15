# 0004 — Phase A3: metadata-service decisions

- **Status:** Accepted · **Date:** 2026-09-15 · **Phase:** A3

Decisions, gap-fills and spec corrections made while implementing Phase A3. None adds a role or a
product feature. Items 6 and 10 add endpoints: the Phase A3 DoD needs the catalog read routes, and
the existing audit owner needs an internal write route.

## Decisions

1. **Service.** `apps/metadata-service` follows Section 4.1, owns the `metadata` schema (Section 8.2
   DDL, migration `0001_metadata_schema`), listens on 8002, and is reachable for users only through
   api-gateway. It re-authenticates the forwarded session or API key through identity-service
   introspection (ADR 0003 item 1) and, when `METADATA_REQUIRE_GATEWAY_TOKEN` is on (refused off in
   staging/prod), requires the gateway's `metadata-service:proxy` service token.

2. **Who touches customer credentials.** Section 13 says query-gateway is "the only service permitted
   to hold or use customer database credentials". Phase A3 and Section 9 ("writes to Vault via
   metadata-service→secrets") put Vault writes, the connectivity test and schema introspection in
   metadata-service. These contradict each other. The phase text is the more specific instruction, so
   it wins, with a narrow boundary. metadata-service stores credentials only in Vault. It holds them in
   memory only while a test or sync runs, and uses them only for fixed `pg_catalog` introspection in a
   read-only transaction. It never runs caller-supplied SQL. **Spec follow-up:** Section 13 should say
   query-gateway is the only service that *executes queries* against customer databases.

3. **What is secret.** Section 13.1 treats host, port and user as leak-sensitive ("raw driver error
   text … can leak host/port/user"). So `POST /data-sources` carries only non-secret metadata (`name`,
   `engine`, `host_label`, `database_name`, `allowed_schemas`). `POST …/secret` carries
   `{host, port, username, password, sslmode}`, and all five go to Vault at
   `secret/data/tenants/<tenant>/datasources/<id>`. That full path is `data_sources.secret_ref`, per the
   Section 8.2 example. Responses never include `secret_ref` or any credential field; the contract test
   enforces this. `host_label` rejects DSN-shaped text. Request models forbid unknown fields, so a
   misplaced `password` is refused rather than stored.

4. **Status lifecycle** (Section 8.2's four values, which the spec does not sequence). Created →
   `pending` (Section 9: "pending until secret set"). Credentials set or rotated → `pending` again,
   because they are unverified. A successful test or sync → `active`; a failed one → `error`.
   `disabled` is only set administratively, and test, sync and credential changes refuse to run on it
   (`409`). A failed test or sync is a result, not an API error: `200` with `ok: false`, a stable
   `code` and a fixed message.

5. **Sync is synchronous for now.** Phase A3 says "initially synchronous for MVP, moved to
   worker-runtime". `POST …/sync` runs the job in the request and returns the
   `metadata.sync.completed` payload shape (`status`, `tables_synced`, plus column and relationship
   counts and the snapshot id). Section 18.1 events are not published until worker-runtime exists.
   When sync moves, this route becomes `202` with a job reference: a versioned contract change.
   Re-sync is diff-based, so table and column ids survive (Section 8.3 stores them as references), and
   so do the fields sync does not own (`is_visible_to_agent`, `is_pii`). Foreign-key relationships are
   rebuilt from the source; `inferred` ones are kept. Concurrent syncs serialize on a row lock. Every
   sync writes a `schema_snapshots` row whose checksum ignores ordering and row-count estimates.

6. **Catalog read routes beyond Section 9.** The DoD requires users to "see tables/columns in the
   catalog API", but Section 9 has no catalog read. Added, all `data:manage` plus the tenant check:
   `GET /data-sources/{id}`, `GET /data-sources/{id}/tables` (keyset-paginated, opaque cursor), and
   `GET /data-sources/{id}/tables/{table_id}` (columns and relationships). Section 2 gives `auditor`
   read access to "connections metadata", but Section 7.1 defines no read permission for it, and
   adding one needs a spec change. `auditor` therefore gets `403` until that permission exists.

7. **What the catalog contains.** `allowed_schemas` must name at least one schema (deny by default),
   and system schemas are refused. Only objects the connected role can actually `SELECT` are catalogued
   (tables, partitioned tables, views, materialized and foreign tables; partitions appear through their
   parent). Schema sync reads structure and the source's own comments, never row data.
   `sample_values` and PII detection belong to profiling in worker-runtime, so `is_pii` stays `false`
   until then. Section 8.2 has no column ordinal, so columns list alphabetically.

8. **Egress control (Section 15).** Data-source connectivity tests are named SSRF vectors. At connect
   time the connector resolves the host, requires every address to be globally routable (loopback,
   RFC 1918/4193, link-local, CGNAT, multicast, reserved, and mapped or 6to4 forms of them are
   refused), and then connects to exactly those addresses. `verify-full` connects by name so the
   certificate can be verified; a rebound name then fails TLS before any credential is sent. IP
   literals are also refused when the credentials are written. The exemption is
   `METADATA_CONNECTOR_ALLOWED_INTERNAL_HOSTS`, whose dev default admits the compose sample database;
   staging/prod refuse loopback entries at startup. The policy is local to this service; it moves into
   a platform package when query-gateway needs the same rule (Phase A4).

9. **DDL corrections to Section 8.2.**
   - `relationships.from_column_id`/`to_column_id` are `ON DELETE CASCADE`. The DDL cascades
     `data_sources → tables → columns`, but a non-cascading reference from `relationships` makes that
     cascade fail as soon as one foreign key is catalogued, which breaks the tenant-deletion cascade
     Section 24 requires. A test proves the full cascade.
   - List-query indexes lead with `tenant_id` (Section 8 preamble), and the relationship FK columns
     are indexed.
   - `columns` has no `tenant_id` in the DDL. Its RLS policy scopes rows through the RLS-protected
     `tables` row (the ADR 0002 `user_roles` pattern). `updated_at` exists, and gets a trigger, only on
     `data_sources` (ADR 0002 item 5).

10. **Audit through its owner.** Section 22 requires audit rows for connection creation and credential
    changes, and Section 7.3 requires before/after state. `identity.audit_events` belongs to
    identity-service, and Section 37 forbids writing another service's tables. identity-service
    therefore adds `POST /internal/v1/audit-events` (scope `identity-service:audit`). A client may only
    write event types under its registered prefixes; metadata-service is limited to `connection.*`.
    Rows are redacted like every other audit row and carry the forwarded `request_id`. metadata-service
    records `connection.created` and `connection.secret_rotated` after the change commits. It retries
    transient failures, and if delivery still fails it logs the event (without state) at ERROR for
    replay rather than undoing a completed change. **Follow-up:** a transactional outbox relayed over
    NATS (Section 18) once the broker is wired.

11. **Shared code.**
    - `packages/python/platform-secrets` is the shared secrets client Phase A3 names, completing
      ADR 0002 item 13. identity-service now uses it and keeps only its reference layout.
    - `platform_auth.IntrospectionClient` is the introspection call shared by api-gateway and
      metadata-service. The gateway's `IdentityClient` became a thin error mapper with unchanged
      behaviour. As a result `platform-auth` now depends on `platform-observability` for request-id
      propagation.

12. **RLS binding per transaction.** Section 19 says to set `app.tenant_id` per request-scoped
    session. metadata-service commits mid-request so it does not hold a transaction open across a
    customer-database round trip, and SQLAlchemy returns the pooled connection on every commit. The
    tenant is therefore bound with a transaction-local `set_config` on every transaction start. It
    cannot go missing on a new pooled connection, and it cannot leak to the next request.

13. **Contract tooling.** A committed operation marked `x-available-in-phase` is a `501` stub that
    promised nothing, so `scripts/openapi_diff.py` no longer counts replacing it with the real contract
    as a breaking change. Removing a stub still counts.

14. **Local stack.** `sample-sales-db` mounts `infra/compose/sample-sales-init/01-sales-schema.sql`: a
    `sales` schema with foreign keys and comments, and a read-only `buvi_reader` role.
    `scripts/seed-sample-sales.sh` re-applies it (part of `make seed`), and `make test-data-sources` runs
    the scripted DoD flow through the real stack.

## Spec errata

- Phase A3: "moved to worker-runtime in **Phase 5**" means Phase A5, where worker-runtime is
  introduced (a stale reference to Section 30's item numbering).

## Known gaps and follow-ups

- **`Idempotency-Key`** (Section 9) is accepted and forwarded but has no replay semantics in any
  service yet (identity-service included). **Scheduled for Phase A5** (see Spec sync below), where
  `POST /conversations/{id}/messages` is the first create-a-run command a browser retries.
- **Per-connection grants** (Section 7.1 "`sql:execute` (per-connection grant)", "approval by admin for
  prod") are enforced by query-gateway (Phase A4) and the admin backend (A10). Phase A3 authorizes on
  `data:manage` plus tenant ownership.
- Sync moves to worker-runtime with `metadata.sync.*` events (Phase A5). Profiling there fills
  `sample_values` and `is_pii`.
- A connection deletion endpoint is not in Section 9 and is not added. The cascade is ready for it.

## Spec sync (2026-09-15, after Phase A3 review)

Applied to `docs/architecture/Agentic_BI_Platform_Build_Spec.md` in its own commit:

- **§13 opening** now reads that query-gateway is the only service permitted to execute arbitrary or
  business SQL with customer credentials, and names metadata-service's narrow exception (bounded
  connectivity check, read-only catalog introspection, never user/developer/agent-originated SQL).
  Resolves item 2's follow-up.
- **§7.1** gains `catalog:read` (`developer`, `org_admin`, `auditor`), and **§9** lists
  `GET /data-sources/{id}`, `GET /data-sources/{id}/tables` and `GET /data-sources/{id}/tables/{table_id}`
  under it with the resource-tenant check. Supersedes item 6: the routes are Section 9 routes and
  `auditor` may read them. Code: `platform_auth.PERM_CATALOG_READ`, gateway catalog, metadata-service
  guards, and the authorization triplet (auditor now in the allowed half for those three routes).
- **§8.2** `relationships` foreign keys are `ON DELETE CASCADE`. Item 9's first correction is now spec.

Open after the sync:

- `GET /data-sources` (the list) stays `data:manage` as Section 9 writes it, so an `auditor` can read a
  data source and its catalog by id but cannot list data sources to discover ids. Moving the list to
  `catalog:read` is a one-row spec change, not made here without the spec owner's decision.
- **Audit delivery has no replay mechanism.** A failed delivery (item 10) is only an ERROR log line
  carrying `event_type`, `resource_type`, `resource_id`, `tenant_id` and `request_id` (not the
  before/after state); a human replays it per the runbook. The transactional outbox remains the fix.
- `Idempotency-Key` replay semantics: Phase A5.
