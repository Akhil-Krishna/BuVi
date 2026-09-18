# 0012 — Phase A9: mcp-gateway (read-only tools first)

- **Status:** Accepted · **Date:** 2026-09-18 · **Phase:** A9
- **Related:** spec commit 36b79bb (plan fixes); Sections 7.3, 8.7, 9, 14, 15, 18.1; [ADR 0011](0011-phase-a8-mysql-connector.md) (the live-instance verification rule).

## Plan issues found before implementation (fixed in the spec)

| Issue | Resolution |
|---|---|
| The DoD needs "an approved server", but MCP approval was assigned to A10, on the premise that step-up did not exist | Step-up exists since A1 (`require_step_up`, used by A3's credential routes). Approval (`org_admin`, step-up) ships in A9. A10 keeps disable/reject. |
| The DoD needs "a `developer` with a grant", but no API created grants | `POST /mcp/servers/{id}/tools/{tool}/grants` and `DELETE …/grants/{grant_id}` (`org_admin`), plus `GET /mcp/servers/{id}` to review the manifest and grants. Added to Section 9. |
| "The gateway discovers tools", but when and who classifies was undefined. Contacting a server at registration is itself a request to a destination nobody has approved (Section 15) | A **declared manifest** at registration (tool names and classes), with no network contact. **Approval discovers and verifies** against the live server: every declared tool must exist, and the server's `readOnlyHint` can only tighten a class. Undeclared tools are unreachable. |
| Section 14 says read-only metadata tools are "allow when source is approved"; Section 9 says invoking needs a "tool grant" | Section 9 wins (deny by default, Section 7). Read classes default to `require_grant`, write/admin to `deny`, and `allow` is reserved and not honoured (`denial()` treats it as deny). |
| "Every invocation (including denials) is recorded", but `mcp.invocations.tool_id` is NOT NULL | Attempts on declared tools are rows. A call naming an undeclared tool has no `tool_id`, so it goes to the audit log (`mcp.tool.denied`, `summary=unknown_tool`) and returns `404`. |
| Section 15's "dedicated egress proxy" cannot be built inside a service | A9 builds the application controls. The proxy and NetworkPolicy are a Phase C1 item, as are per-tenant MCP concurrency limits (Section 24). |
| `developer` has `mcp:manage` only "if granted", and per-user permission grants are A10 | In A9 `org_admin` registers servers; the DoD's developer *invokes* with a tool grant. |

## Decisions

1. **mcp-gateway** (port 8009, schema `mcp`):
   - Section 8.7 DDL as written, with RLS on every table. `mcp.tools` has no tenant column, so it is isolated through its server.
   - `mcp.invocations` is append-only for the request role (SELECT and INSERT grants only), so a security record is never rewritten.
2. **Own MCP client, tested against the official SDK.** A ~250-line Streamable HTTP client (JSON-RPC: `initialize`, `notifications/initialized`, `tools/list` with paging, `tools/call`; JSON or SSE responses; protocol versions 2025-03-26 to 2025-11-25). It was written rather than taken from the SDK so that every byte crosses our egress layer. It is stateless per call and ends sessions with a best-effort `DELETE`. The tests run the SDK's FastMCP server in both response modes (the Section 13.1 rule applied to a protocol).
3. **Section 15 at request time** (`infrastructure/mcp/egress.py`):
   - The host is resolved at every call, and every address must be public.
   - The request goes to the checked address, with the hostname only in `Host` and SNI. The certificate is still verified against the hostname; a test resolves an impostor name to a real server and TLS fails.
   - **No keep-alive:** two tenants' servers can share an address, and a pooled TLS connection verified for one hostname must never carry another's request.
   - No redirects, no environment proxy, no retries.
   - Bodies are JSON or SSE only, capped on raw bytes. The SSE reader counts bytes itself, because httpx's line iterator would buffer an endless line whole.
   - One overall deadline per call.
4. **Registration refuses what the text reveals.** Non-HTTPS (plain HTTP only for allow-listed internal hosts), credentials, queries or fragments in the URL, non-public IP literals including IPv4-mapped and 6to4 forms, and numeric host spellings that resolvers expand (`2130706433`, `0x7f.1`, `0177.0.0.1`).
5. **Invocation order and record.**
   - Order: approved, then policy, then grant, then the call.
   - A denial, including a request-time `DESTINATION_NOT_ALLOWED`, is written and committed before the `403`, audited, and published as `mcp.invocation.denied` (best effort; readiness degrades without NATS).
   - Upstream failures are recorded as `error:upstream:<reason>`, and a Vault failure as `error:secret_store_unavailable`.
   - No transaction is held across the call.
6. **Output is untrusted and never stored.**
   - The response carries `untrusted: true`, text blocks and structured content. Other block types are dropped and counted.
   - `response_summary` is status and sizes only; arguments are stored, capped at 16 KiB.
   - The sample server's `search_docs` returns a prompt-injection sentence, and the tests assert it comes back as data.
7. **Bearer tokens** go to Vault (`mcp/<tenant>/<server>`). They are read only when sent, and only to the pinned server. Tests prove they never appear in a response, row, log or audit event.
8. **Role checks.** Approval and grants re-check `org_admin` in the service as well as at the gateway. Section 9 names the role; no permission separates approving from registering.

## Found while building and reviewing (fixed before commit)

- The SSE reader used httpx's `aiter_lines`, which buffers a whole line before the size cap can see it. It now caps raw bytes; `/endless/mcp` streams 4 MiB without a newline and is refused at the cap.
- `allow` would have granted access without a grant if a row ever held it. It is now denied until defined.
- A stored endpoint rejected by changed egress settings would have been an unrecorded `500`. It is now a recorded `destination_not_allowed` denial.
- A Vault failure during an allowed invocation left no `mcp.invocations` row. It is now recorded (regression test).
- The live-flow runner expanded service env words unquoted, so a JSON list value was also a glob pattern. Globbing is now off (`set -f`).

## Verification

- **mcp-gateway: 96 tests.**
  - The endpoint corpus: 42 refused spellings.
  - Policy units.
  - The DoD journey in both MCP response modes over real TLS.
  - The SSRF suite over HTTP:
    - internal addresses at approval;
    - mixed public and private records;
    - DNS rebinding after approval, for six internal targets, with no packet sent;
    - a redirect to the metadata address;
    - `text/html` and endless bodies;
    - TLS name mismatch under pinning;
    - unresolvable and unreachable hosts.
  - Grants, step-up, cross-tenant `404`s, append-only rows, output caps, tool errors, and the contract.
- **Live** (`make test-mcp`, through api-gateway, against the SDK sample server):
  - the registration SSRF corpus;
  - an unapproved server denied;
  - approval by `org_admin` with step-up, and a developer refused;
  - a grantee allowed; a user without a grant and a `write` tool denied;
  - every attempt in `mcp.invocations`, with audit events and three `mcp.invocation.denied` messages on stream `MCP`;
  - a Vault-held token that appears in no response, log or row.

## Gaps

- DNS-controlled cases (rebinding, redirect, TLS pinning) are proven in the integration suite, not in the live flow, which has no DNS control.
- Server disable/reject, write-tool confirmation and per-user `mcp:manage` are Phase A10. The egress proxy and per-tenant MCP limits are Phase C1.
- Server-initiated requests inside a response stream (sampling, elicitation) are not answered; such a call ends at the deadline.
