"""Connect-time destination check and pinning (Section 15).

Resolve the endpoint's host now, require every answer to pass `EgressPolicy`, and hand back the
first address: the caller connects to exactly that address, with the hostname only in the `Host`
header and as TLS SNI, so a DNS answer that changes after the check (rebinding) cannot redirect
the connection and certificate verification is still against the hostname.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from platform_egress.endpoint import Endpoint
from platform_egress.policy import EgressPolicy, IPAddress

Resolver = Callable[[str, int], Awaitable[list[IPAddress]]]


class DestinationNotAllowedError(Exception):
    """The endpoint now resolves to an address Section 15 refuses."""


@dataclass(frozen=True)
class PinnedEndpoint:
    endpoint: Endpoint
    address: IPAddress

    @property
    def request_url(self) -> str:
        host = f"[{self.address}]" if self.address.version == 6 else str(self.address)
        return f"{self.endpoint.scheme}://{host}:{self.endpoint.port}{self.endpoint.path}"

    @property
    def extensions(self) -> dict[str, str]:
        """TLS SNI (and so certificate verification) against the hostname, not the address."""
        if self.endpoint.scheme == "https" and ":" not in self.endpoint.host:
            return {"sni_hostname": self.endpoint.host}
        return {}


async def pin_endpoint(
    endpoint: Endpoint, egress: EgressPolicy, resolver: Resolver
) -> PinnedEndpoint:
    """Raises `HostResolutionError` (from the resolver) or `DestinationNotAllowedError`."""
    addresses = await resolver(endpoint.host, endpoint.port)
    if not egress.permits(endpoint.host, addresses):
        raise DestinationNotAllowedError()
    return PinnedEndpoint(endpoint, addresses[0])
