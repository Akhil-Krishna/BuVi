# notification-service

In-app, email and signed-webhook notifications from platform events (build spec Sections 8.8, 15,
18.1; Phase A11).

| | |
|---|---|
| Owner | platform / integrations |
| Schema | `notification` (Section 8.8: `notifications`, `webhook_subscriptions`; RLS; no deletes for the request role) |
| Port | 8010 |
| Public API (via api-gateway) | `GET /api/v1/me/notifications`, `POST …/{id}/read` (session, own only); `POST/GET /api/v1/admin/webhooks`, `DELETE …/{id}` (`org_admin`; create and disable need step-up) |
| Events consumed | `dashboard.tile.pinned`, `mcp.invocation.denied`, `metadata.sync.completed`, `identity.role.changed` (durable JetStream pull consumers) |
| Contract | `contracts/openapi/notification-service.json` |
| Health | `/health/live`; `/health/ready` (Postgres; the consumers only degrade it) |
| Dependencies | Postgres, identity-service (introspection, directory, audit), Vault (signing secrets), NATS, SMTP, tenants' webhook receivers |
| Decisions | [ADR 0014](../../docs/adr/0014-phase-a11-notifications-webhooks-usage.md) |
| Runbook | [`docs/runbooks/notification-service.md`](../../docs/runbooks/notification-service.md) |

## What it consumes

| Topic (stream) | Recipients | Channels |
|---|---|---|
| `dashboard.tile.pinned` (DASHBOARD) | the pinner | in-app |
| `mcp.invocation.denied` (MCP) | every active `org_admin` | in-app, email |
| `metadata.sync.completed` (METADATA) | whoever ran the sync | in-app, email |
| `identity.role.changed` (IDENTITY) | the affected user | in-app, email |

- Recipients and addresses come from identity-service's `POST /internal/v1/directory/users`
  (scope `identity-service:directory`).
- Each event is keyed `<stream>:<sequence>`. A row is unique per (event, user, channel), so a
  redelivered event never notifies twice.
- Durable consumers start at new messages the first time. After that they resume where they left
  off.

## Webhooks

- `POST /api/v1/admin/webhooks` needs `org_admin` and a fresh step-up.
  - Event types must be on the allow-list: `dashboard.tile.pinned`, `metadata.sync.completed`,
    `mcp.invocation.denied`.
  - The URL is checked under Section 15 (`platform_egress.parse_endpoint`).
  - The signing secret goes to Vault at `tenants/{t}/notification/webhooks/{id}` and is shown
    once.
- `GET /api/v1/admin/webhooks` lists subscriptions and never returns the secret.
- `DELETE /api/v1/admin/webhooks/{id}` needs a step-up. It disables the subscription and deletes
  the secret.
- Every delivery:
  - re-checks the URL, resolves it now and pins the checked address (TLS still verified against
    the hostname);
  - follows no redirects;
  - makes up to 3 bounded attempts and never reads the response body;
  - is signed `X-Buvi-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>." + body)>`, with
    `X-Buvi-Event` and `X-Buvi-Delivery` (the notification row id);
  - is recorded as a `webhook` notification row for the subscription's creator.

## Inbox

- `GET /api/v1/me/notifications?unread_only=&limit=&cursor=` returns the caller's in-app items,
  newest first, rendered to title and body, plus the unread count.
- `POST /api/v1/me/notifications/{id}/read` marks one read. Anyone else's id is `404`.

## Develop

```bash
make dev-notification            # :8010, with reload
uv run pytest apps/notification-service
```
