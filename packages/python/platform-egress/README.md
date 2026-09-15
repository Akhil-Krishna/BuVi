# platform-egress

Section 15 outbound-network controls shared by every service that opens a connection a tenant can
point somewhere: metadata-service (connectivity test, catalog sync) and query-gateway (query
execution). Extracted in Phase A4 (ADR 0004 item 8, ADR 0005).

Contract:

- `EgressPolicy`: `permits(host, addresses)` at connect time, `permits_literal(host)` at
  registration time, `from_hosts(...)` for the explicit internal allow-list.
- `is_public_address(address)`: globally routable, and not multicast. Blocks loopback,
  RFC 1918/4193, link-local, CGNAT, reserved, unspecified, and mapped/6to4 forms of those.
- `resolve_host(host, port)`: resolve once, IPv4 first. Raises `HostResolutionError` with no detail.
  Callers validate the result and connect to exactly those addresses (DNS rebinding defense).

Pure standard library. The SSRF-safe HTTP client for MCP and webhooks (Section 33) joins this
package when those phases land.
