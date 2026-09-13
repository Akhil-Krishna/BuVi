"""OpenTelemetry bootstrap, logging config, and SSRF-safe egress (Sections 15, 22, 33).

Contract: `request_id`/`run_id` correlation, W3C trace-context propagation, and
the single outbound-fetch client that enforces the Section 15 SSRF controls.
No service performs an ad-hoc `httpx.get` on a user's behalf.
"""
