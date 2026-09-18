# mcp-gateway

Governed MCP integrations: server registry, tool classification and policy, and SSRF-safe invocation proxying (build spec Sections 8.7, 14, 15; Phase A9).

| | |
|---|---|
| Owner | platform / integrations |
| Schema | `mcp` (Section 8.7: `servers`, `tools`, `tool_grants`, `invocations`; RLS; `invocations` append-only for the request role) |
| Port | 8009 |
| Public API (via api-gateway) | `GET/POST /api/v1/mcp/servers` (`mcp:manage`), `GET /api/v1/mcp/servers/{id}` (`mcp:manage`), `POST …/{id}/approve` (`org_admin`, step-up), `POST …/{id}/tools/{tool}/grants` and `DELETE …/grants/{grant_id}` (`org_admin`), `POST …/{id}/tools/{tool}/invoke` (tool grant) |
| Events | `mcp.invocation.denied` on JetStream stream `MCP` (`contracts/events/mcp.invocation.denied.v1.json`) |
| Contract | `contracts/openapi/mcp-gateway.json` |
| Health | `/health/live`; `/health/ready` (Postgres; the event stream only degrades it) |
| Dependencies | Postgres, identity-service (introspection, audit), Vault (server tokens), NATS, the tenants' MCP servers |
| Decisions | [ADR 0012](../../docs/adr/0012-phase-a9-mcp-gateway.md) |
| Runbook | [`docs/runbooks/mcp-gateway.md`](../../docs/runbooks/mcp-gateway.md) |

## Lifecycle

1. **Register.** Name, HTTPS endpoint, optional bearer token, and the tools the tenant will use, with their classes. The server is created `pending_approval`; nothing is contacted.
2. **Approve** (`org_admin` + fresh MFA). The gateway discovers the live tools and checks the declared manifest: every declared tool exists, and none declared read-class is marked `readOnlyHint: false`. On any mismatch the server stays pending.
3. **Grant.** Grant a read-class tool to a tenant role or to a user. Write and admin tools cannot be granted until Phase A10.
4. **Invoke.** The checks run in order: server approved, tool not `deny`, caller granted. The call is then made under the outbound controls below. Every attempt is one `mcp.invocations` row plus an audit event; denials are also published. Output is returned marked `untrusted` and is never stored.

## Outbound controls (Section 15)

- HTTPS only; plain HTTP is allowed only for hosts in `MCP_EGRESS_ALLOWED_INTERNAL_HOSTS` (dev and tests).
- The host is resolved when each call is made, and every address must be public, so DNS rebinding cannot reach an internal address.
- The request goes to the checked address; the certificate is verified against the hostname.
- Redirects are never followed. Each request uses a fresh connection. Time, response size and content type (JSON or SSE) are capped.

## Run and test

```bash
make dev-mcp
uv run --package mcp-gateway pytest apps/mcp-gateway/src/mcp_gateway/tests
make test-mcp              # Phase A9 DoD against the real stack
```
