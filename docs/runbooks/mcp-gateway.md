# Runbook: mcp-gateway

**Owner:** platform / integrations · **Pages on:**
- readiness failing;
- a spike in `mcp.invocation.denied` (Section 22.1: more than 5x the rolling baseline is an abuse signal);
- any `DESTINATION_NOT_ALLOWED` denial on an approved server (a name that moved to an internal address).

## Health

- `GET /health/live`: the process is up.
- `GET /health/ready`: returns `503` when Postgres is unavailable.
- `checks.events: degraded`: NATS is unreachable. Denials are still recorded, both as `mcp.invocations` rows and in the audit log; only the alert stream is missing.

## How it is used

1. **Register** (`mcp:manage`). An HTTPS endpoint and the declared tool manifest (name and class for each tool). The server is not contacted. An optional bearer token goes to Vault at `mcp/<tenant>/<server>`, and the row keeps only that reference.
2. **Approve** (`org_admin` with fresh MFA). The gateway discovers the tools over MCP. Every declared tool must exist, and none declared read-class may be marked `readOnlyHint: false`. The approval audit event lists any undeclared tools the server offers; they are never reachable.
3. **Grant** (`org_admin`). Read-class tools go to a tenant role or a user. `write`/`admin` tools are refused until Phase A10.
4. **Invoke** (grantee). Checks run in order: approved server, tool policy, grant. The call is then made under the Section 15 controls. Each attempt produces one `mcp.invocations` row plus one audit event (`mcp.tool.invoked` or `mcp.tool.denied`).

## Common incidents

| Symptom | Likely cause | Action |
|---|---|---|
| Approval `422 MCP_MANIFEST_MISMATCH` | A declared tool is missing, or the server marks a declared read tool as not read-only | Expected. Fix the manifest by re-registering; never approve by editing rows. |
| Approval or invoke `502`, `details.reason=unreachable` | Server down, TLS failure, or its certificate does not name the host | Check the server. A TLS name mismatch is a refusal by design. |
| `502 redirect_refused` | The server answered with a redirect | Register the final URL. Redirects are never followed. |
| `502 response_too_large` / `content_type_not_allowed` | Output over `MCP_MAX_RESPONSE_BYTES`, or not JSON/SSE | Ask for smaller results; raise the cap only knowingly. |
| `403 DESTINATION_NOT_ALLOWED` on an approved server | Its hostname now resolves to a private, loopback, link-local or metadata address | **Treat as an incident** (possible DNS rebinding). Keep denying. Contact the tenant. |
| `503 SECRET_STORE_UNAVAILABLE` | Vault unreachable, or the server's secret was removed | Restore Vault. The attempt is recorded as `error:secret_store_unavailable`. |
| `404 MCP_TOOL_NOT_FOUND` spikes | Callers probing undeclared tools | They appear in `identity.audit_events` as `mcp.tool.denied` with `summary=unknown_tool`. |

## Operations

- **Who invoked what:** `SELECT created_at, invoked_by, response_status, response_summary FROM mcp.invocations WHERE tool_id = '<tool>' ORDER BY created_at DESC;` Run as `buvi_migrator`, or as the app role with `app.tenant_id` set. The request role cannot update or delete these rows.
- **Revoke access now:** `DELETE /api/v1/mcp/servers/{id}/tools/{tool}/grants/{grant_id}`. Disabling a whole server is Phase A10; until then, revoke its grants.
- **Internal MCP servers** (e.g. a sidecar) need their hostname in `MCP_EGRESS_ALLOWED_INTERNAL_HOSTS` (a JSON list), recorded as an approved exception. Those hosts skip the public-address rule and may use plain HTTP. Startup refuses loopback there in staging/prod.
- **Egress:** in production, mcp-gateway's outbound traffic must go through the dedicated egress proxy with its NetworkPolicy (Section 15, a Phase C1 item). The application controls do not replace it.

## Deploy / rollback

1. `alembic upgrade head` as `buvi_migrator`, then roll the deployment. Register `mcp-gateway` as a service client in identity-service (introspection plus `mcp.` audit events), and give api-gateway the `mcp-gateway:proxy` audience.
2. Rollback: redeploy the previous image. `alembic downgrade -1` drops every registration and the invocation history; use it only for a failed first deployment.
