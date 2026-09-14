"""Telemetry identifiers for identity-service (Section 22).

Tracing is wired through `platform_observability`; request ids are minted there.
"""

from __future__ import annotations

from platform_observability.correlation import new_request_id

__all__ = ["new_request_id"]
