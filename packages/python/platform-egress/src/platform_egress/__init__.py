"""Outbound-destination policy and connect-time resolution (Section 15).

Contract: `EgressPolicy`, `is_public_address`, `IPAddress`, `resolve_host`, `HostResolutionError`.
"""

from platform_egress.policy import EgressPolicy, IPAddress, is_public_address
from platform_egress.resolver import HostResolutionError, resolve_host

__all__ = ["EgressPolicy", "HostResolutionError", "IPAddress", "is_public_address", "resolve_host"]
