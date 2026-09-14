"""OpenTelemetry bootstrap placeholder (Section 22).

Tracing is wired repo-wide in `packages/python/platform-observability` once a
second service exists to correlate against (Phase A2 introduces api-gateway and
the `request_id` propagation path it anchors). Until then this module owns the
identifiers Section 22 requires, so callers import a stable name today and gain
real spans without a call-site change.
"""

from __future__ import annotations

import uuid


def new_request_id() -> str:
    """Generate a correlation id for one inbound HTTP request (Section 22)."""
    return f"req_{uuid.uuid4().hex}"
