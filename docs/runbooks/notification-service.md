# Runbook: notification-service

**Owner:** platform / integrations · **Pages on:**
- readiness failing;
- `checks.events: degraded` for more than 5 minutes (events are queuing unconsumed);
- webhook deliveries failing with `destination_not_allowed` (a subscribed hostname that moved to an internal address).

## Health

- `GET /health/live`: the process is up.
- `GET /health/ready`: returns `503` when Postgres is unavailable.
- `checks.events: degraded`: at least one topic consumer is not connected to NATS. The inbox and webhook management still work. Events wait in their streams and are consumed on reconnect, up to `max_deliver` (10) attempts each.

## What it consumes and sends

| Topic (stream) | Recipients | Channels | Webhook-able |
|---|---|---|---|
| `dashboard.tile.pinned` (DASHBOARD) | the pinner | in-app | yes |
| `mcp.invocation.denied` (MCP) | every active `org_admin` | in-app, email | yes |
| `metadata.sync.completed` (METADATA) | whoever ran the sync | in-app, email | yes |
| `identity.role.changed` (IDENTITY) | the affected user | in-app, email | never |

- **Durable consumers:** named `notification-service-<subject with dashes>`. A new durable starts at new messages. An existing one resumes where it stopped.
- **Idempotency:** each message's key is `<stream>:<stream sequence>`. Rows are unique per (key, user, channel), so a redelivery never notifies twice.
- **Crash recovery:** an email a crash left `queued` is sent on redelivery.
- **Recipients:** looked up in identity-service (`POST /internal/v1/directory/users`, scope `identity-service:directory`); inactive users are skipped.
- **Invitation email:** sent by identity-service (ADR 0014), not here.

## Common incidents

| Symptom | Likely cause | Action |
|---|---|---|
| Log `directory unavailable; will retry` repeating | identity-service down, or the `notification-service` client lacks `identity-service:directory` | Restore identity. Events are retried every `NOTIFICATION_RETRY_SECONDS` (10 s) and dropped after 10 attempts. |
| Email rows `failed`, `payload.delivery = smtp_failed` | SMTP unreachable or refusing | Fix SMTP (`NOTIFICATION_SMTP_*`). Failed rows are not resent automatically; the in-app copy exists. |
| Webhook rows `failed`, `delivery = destination_not_allowed:1` | The subscription's hostname now resolves to a private, loopback, link-local or metadata address | **Treat as an incident** (possible DNS rebinding). Nothing was sent. Contact the tenant; disable the subscription if unexplained. |
| Webhook rows `failed`, `delivery = status_5xx:3`, `timeout:3` or `unreachable:3` | Receiver down (three attempts were made) | The tenant's problem. There is no replay queue yet: rows show what was missed. |
| `redirect_refused:1` | The receiver answered with a redirect | The tenant must register the final URL. Redirects are never followed. |
| `secret_unavailable` | Vault unreachable, or the subscription's secret was removed | Restore Vault. Deliveries are never sent unsigned. |
| `422 WEBHOOK_URL_INVALID` on create | The URL breaks a Section 15 rule (`details.reason`) | Expected. HTTPS to a public host only. |
| `409 WEBHOOK_LIMIT_REACHED` | `NOTIFICATION_MAX_WEBHOOKS_PER_TENANT` (10) active subscriptions | Disable unused ones. |

## Operations

- **What was delivered to a user:** `SELECT created_at, channel, template_key, status, payload->>'delivery' FROM notification.notifications WHERE user_id = '<user>' ORDER BY created_at DESC;` Run as `buvi_migrator`, or as the app role with `app.tenant_id` set.
- **Webhook history of a subscription:** filter `channel = 'webhook' AND payload->>'subscription_id' = '<id>'`.
- **Stop a webhook now:** `DELETE /api/v1/admin/webhooks/{id}` (`org_admin` + step-up). It disables the subscription and deletes its signing secret from Vault (`tenants/<t>/notification/webhooks/<id>`).
- **Receivers verify:** `X-Buvi-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>." + raw body)>`. Receivers should reject old timestamps. `X-Buvi-Delivery` is the notification row id: deduplicate on it.
- **Internal webhook receivers** need their hostname in `NOTIFICATION_EGRESS_ALLOWED_INTERNAL_HOSTS` (a JSON list), recorded as an approved exception. Those hosts skip the public-address rule and may use plain HTTP. Startup refuses loopback there in staging/prod.
- **Egress:** in production, webhook traffic must go through the dedicated egress proxy with its NetworkPolicy (Section 15, a Phase C1 item). The application controls do not replace it.

## Deploy / rollback

1. Run `alembic upgrade head` as `buvi_migrator`, then roll the deployment.
   - Register the `notification-service` service client in identity-service: introspection, `identity-service:directory`, and `webhook.` audit events.
   - Give api-gateway the `notification-service:proxy` audience.
2. Rollback: redeploy the previous image. Consumers resume from their durables.
   - `alembic downgrade -1` drops every notification and subscription (their Vault secrets remain). Use it only for a failed first deployment.
