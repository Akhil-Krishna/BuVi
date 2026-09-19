# 0014 — Phase A11: notification-service, signed webhooks, billing usage

- **Status:** Accepted · **Date:** 2026-09-19 · **Phase:** A11
- **Related:**
  - spec commits 435895c (replan) and b537537 (subscriptions to post-GA);
  - Sections 3, 8.4, 8.8, 9, 15, 18.1, 23;
  - [ADR 0002](0002-phase-a1-identity-decisions.md) (invitation email);
  - [ADR 0004](0004-phase-a3-metadata-service.md) (sync runs in-request);
  - [ADR 0012](0012-phase-a9-mcp-gateway.md) (SSRF controls);
  - [ADR 0013](0013-phase-a10-authorization-completion.md) (webhooks moved here).

## Plan issues found before implementation (fixed in the spec)

| Issue | Resolution |
|---|---|
| "Wire it as a consumer of the topics that list notification-service", but two of the five had no producer: `metadata.sync.completed` (sync still runs in-request) and `identity.role.changed` | Producers added: metadata-service publishes sync completion while sync is in-request; identity-service publishes role changes. The producer column in 18.1 records it. |
| `query.completed` is listed, but queries are synchronous: nobody waits on one, so there is nothing to notify | Not produced or consumed yet. It arrives with async query/export execution (post-GA). |
| No routing: which event notifies whom, on which channel | Written into A11 (and the service README): pin -> the pinner, in-app; MCP denial -> every active `org_admin`, in-app and email; sync -> whoever ran it, in-app and email; role change -> the affected user, in-app and email. |
| `sync.completed`'s payload named nobody to notify; `role.changed`'s `{user_id, role, granted_by}` cannot describe a grant-and-revoke | `user_id` (who ran the sync) and the source's name were added; role changes carry `roles`, `granted`, `revoked`, `changed_by`. |
| No idempotency: JetStream redelivers | `notifications.event_key` (`<stream>:<sequence>`) with `UNIQUE (event_key, user_id, channel)`. |
| A webhook delivery row needs a `user_id` (NOT NULL) | `webhook_subscriptions.created_by`; delivery rows are recorded against it. |
| "Rejected for a non-allow-listed destination": the allow-list was undefined for webhooks | The subscription itself, created by an `org_admin` with a fresh step-up, is the per-tenant approval. Section 15's address rules apply at creation and again at every delivery. A second, fixed allow-list governs **event types**. |
| `/me/notifications` had no way to mark read (Phase B7 needs it); webhooks could be created but never listed or stopped | `POST /me/notifications/{id}/read`, `GET /admin/webhooks`, `DELETE /admin/webhooks/{id}`. |
| `/billing/usage` had no owner or storage ("aggregate in worker-runtime", which has no database) | worker-runtime consumes and writes through analytics-orchestrator, which already owns the token ledger and `/billing/quotas`. The table is `analytics.usage_records`. |
| "Query minutes" had no producer | query-gateway meters `query_execution_ms`. Seats are read live from identity (a gauge, not an event). Storage bytes wait for artifact storage metering (post-GA). |
| `POST /billing/subscription` was a stub labelled A11, but nothing specifies a payment provider or an owner | Post-GA backlog (b537537). It stays a documented `501`. |

## Decisions

1. **notification-service consumes directly** (Section 18.1, Phase A11).
   - Section 3's worker-runtime row mentions "notification dispatch". Nothing here is bulk or scheduled, so it would only add a hop.
   - There is one durable pull consumer per topic, on the producer's stream, declared with the producer's exact config.
   - A new durable starts at `DeliverPolicy.NEW`: a year-old pin is not news. An existing one resumes where it stopped.
2. **Delivery semantics.**
   - A malformed message or an unknown major version is terminated.
   - A directory or database failure retries the whole event (`nak`, 10 s, at most 10 deliveries). The unique rows make the retry idempotent.
   - A per-delivery failure (SMTP, receiver) is recorded as `failed`, and the event is done.
   - An email a crash left `queued` is sent on redelivery.
3. **Recipients come from identity-service's directory** (`POST /internal/v1/directory/users`, scope `identity-service:directory`).
   - It is RLS-bound to the tenant in the request, and inactive users are skipped.
   - It is the only path by which emails leave identity. analytics-orchestrator uses it too, for seats.
4. **Invitation email stays in identity-service.**
   - This reverses ADR 0002's "A11 replaces it".
   - The invitation carries a one-time token that must not travel over the event bus or sit in another service's store, and the invitee is not yet a user the directory could resolve.
5. **Producers publish after the commit, best effort.**
   - metadata-service: after the sync commits.
   - identity-service: as a background task, which runs after the response and so after the dependency's commit. A rolled-back change is never announced.
   - query-gateway: after the concurrency slot is released.
   - Every one fails fast without NATS, and none makes its request fail. The `*_EVENTS_ENABLED` / `METERING_ENABLED` switches exist for tests.
6. **Webhooks.**
   - Creation needs `org_admin` and a fresh step-up. The URL goes through `platform_egress.parse_endpoint`: HTTPS to a public host, unless the host is on the internal allow-list.
   - There are at most 10 active subscriptions per tenant.
   - The secret is `whsec_` plus 32 random bytes, stored in Vault at `tenants/<t>/notification/webhooks/<id>`. It is shown once with `no-store` and never listed. Disabling deletes it.
   - Each delivery re-parses the URL and resolves it now, then connects to the checked address, with the hostname only in `Host` and TLS SNI.
   - There are no redirects, no keep-alive and no proxy, and the response body is never read.
   - At most 3 attempts are made. `destination_not_allowed`, `redirect_refused` and `url_invalid` are final.
   - Each delivery is signed `X-Buvi-Signature: t=<unix>,v1=hex(HMAC-SHA256(secret, "<t>." + body))`, and the headers include `X-Buvi-Delivery` (the row id). A delivery is never sent unsigned.
   - Webhook-able events: `dashboard.tile.pinned`, `metadata.sync.completed`, `mcp.invocation.denied`. **Role changes never leave the platform**: they describe a person's access.
7. **`platform-egress` owns endpoint parsing and connect-time pinning.**
   - `parse_endpoint`, `pin_endpoint` and `PinnedEndpoint` moved there from mcp-gateway instead of being copied, so the two outbound paths cannot drift apart.
   - Each service keeps its own HTTP client: MCP streams, while webhooks only post.
8. **Usage.**
   - `BillingUsageRecorded` 1.1 adds `event_id` (the idempotency key, also the JetStream message id) and `occurred_at`, and makes `model` optional for `query_execution_ms`.
   - The worker batches up to 100 messages and acks only after the store succeeded. There is no delivery limit: usage is never dropped for an outage.
   - `GET /billing/usage?start=&end=` takes inclusive UTC dates, defaults to this month and allows at most 366 days. It returns tokens (and by stage), query minutes and live seats.

## Found while building and reviewing (fixed before commit)

- **Metering held query capacity.**
  - The first cut published usage inside the tenant's concurrency slot. A slow or reconnecting NATS (3 s ack timeout) would have held the slot and delayed every query.
  - It now publishes after the slot is released, and fails fast when disconnected.
- **Old events would have replayed into the A11 flow.**
  - notification-service's durables outlive a run. In `make test-live` they would have delivered every sync, pin and denial that earlier flows published while the service was down.
  - `reset_demo_state` now deletes those durables (they restart at `NEW`) and clears notification rows and subscriptions. `assert_demo_baseline` checks both.
- **A1's live flow checked a stub that A11 made real** (`GET /me/notifications`, as `/dashboards` was in A6). The check moved to the last stub left, `POST /billing/subscription`, after the admin's step-up.
- **Stale forward references:** comments saying notification-service "arrives in A11", and identity's claim that A11 would take over invitation email (see decision 4).

## Verification

- **Unit and integration suites:**

  | Suite | Tests | Covers |
  |---|---|---|
  | notification-service (new) | 22 | Dispatch for every topic, idempotency, inactive recipients, directory outage, SMTP failure; inbox paging and owner-only reads; webhook signing (verified with the returned secret), the Section 15 refusals at creation, DNS rebinding at delivery (never contacted), bounded attempts, redirects, step-up and org_admin, cross-tenant 404, the cap; a real-NATS consumer that does not replay history |
  | worker-runtime | 18 | Usage batches against real NATS: retried while the store is down, acked after it succeeds |
  | analytics-orchestrator | 84 | Idempotent usage store, period sums, seats, refusals |
  | identity-service | 253 | Directory; role events published once, and only on change |
  | metadata-service | 174 | Sync events on success and failure |
  | query-gateway | 543 | Metering per executed query, never for a rejected one |
  | api-gateway | 298 | — |
  | platform-egress and mcp-gateway | 130 | — |

- **Contracts:** `contracts/events/metadata.sync.completed.v1.json` and `identity.role.changed.v1.json` are new; `billing.usage.recorded` is 1.1. OpenAPI regenerated with no breaking changes.
- **Live:** all eleven flows pass in one `make test-live` run. `make test-notifications` (every service real, MailHog, NATS, Vault; the script is the webhook receiver) proves the DoD over HTTP and the queue:
  - a sync completion, a dashboard pin and a failed MCP invocation each produce the right in-app notification and (sync, denial) MailHog email;
  - a webhook subscribed to them receives payloads whose signature verifies with the shown-once secret;
  - `127.0.0.1`, metadata, private and plain-HTTP destinations, and a non-allow-listed event type, are refused;
  - role changes notify the user and never leave by webhook;
  - a disabled webhook receives nothing;
  - `/billing/usage` reports the chat run's tokens and query time via worker-runtime.

## Gaps

- **No webhook replay queue:** a receiver down for all three attempts misses that event; its row records it. The same goes for failed emails, which are not resent. This is a Phase C1 hardening candidate, alongside the egress proxy.
- **The directory returns at most 5000 users per call.** The seat count is exact only below that. It needs a count endpoint before large tenants.
- **Legacy usage events:** `billing.usage.recorded` 1.0 messages already in a stream (none in production) get a random `event_id` when parsed, so a redelivery of one of those would be counted twice.
- **Carried from ADR 0013:** a deactivated user's share links stay live. A `user.deactivated` event consumed by dashboard-service would close that; it is still open.
