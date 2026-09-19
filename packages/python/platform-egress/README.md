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

- `parse_endpoint(url, policy)` -> `Endpoint`: registration-time URL rules (HTTPS unless the host is
  allow-listed, no credentials/query/fragment, no blocked IP literal or numeric host spelling).
  Raises `EndpointRejected` with a fixed `reason`. Used by mcp-gateway (servers) and
  notification-service (webhooks); moved here from mcp-gateway in Phase A11.
- `pin_endpoint(endpoint, policy, resolver)` -> `PinnedEndpoint`: resolve now, check every answer,
  connect to the first. `request_url` targets the address; `extensions` keep TLS SNI (and
  certificate verification) on the hostname. Raises `DestinationNotAllowedError` or
  `HostResolutionError`.

Pure standard library: each service keeps its own HTTP client (MCP streams responses; webhooks
only post), built with no redirects, no keep-alive and no environment proxies.
