"""Outbound-destination policy, endpoint parsing and connect-time pinning (Section 15).

Contract: `EgressPolicy`, `is_public_address`, `IPAddress`, `resolve_host`, `HostResolutionError`,
`parse_endpoint`, `Endpoint`, `EndpointRejected`, `pin_endpoint`, `PinnedEndpoint`,
`DestinationNotAllowedError`.
"""

from platform_egress.endpoint import MAX_URL_LENGTH, Endpoint, EndpointRejected, parse_endpoint
from platform_egress.pinning import (
    DestinationNotAllowedError,
    PinnedEndpoint,
    Resolver,
    pin_endpoint,
)
from platform_egress.policy import EgressPolicy, IPAddress, is_public_address
from platform_egress.resolver import HostResolutionError, resolve_host

__all__ = [
    "MAX_URL_LENGTH",
    "DestinationNotAllowedError",
    "EgressPolicy",
    "Endpoint",
    "EndpointRejected",
    "HostResolutionError",
    "IPAddress",
    "PinnedEndpoint",
    "Resolver",
    "is_public_address",
    "parse_endpoint",
    "pin_endpoint",
    "resolve_host",
]
