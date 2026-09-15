"""Outbound-destination policy (Section 15).

Section 15 names every user-directed outbound connection an SSRF vector: a tenant who can
set a host can otherwise make the platform open connections to the cluster's own services.
Rules:

* every resolved address must be globally routable -- loopback, private (RFC 1918 /
  RFC 4193), link-local, CGNAT, multicast, reserved and unspecified addresses are
  refused, including IPv4-mapped IPv6 forms of them;
* the check runs on the addresses resolved *at connect time*, and the connector then
  connects to exactly those addresses (DNS rebinding defense);
* an explicitly allow-listed internal host is exempt ("unless the destination is an
  explicitly allow-listed internal service").

Pure: no DNS, no sockets. Resolution is `platform_egress.resolve_host`.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def is_public_address(address: IPAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped or address.sixtofour
        if mapped is not None:
            return is_public_address(mapped)
    return address.is_global and not address.is_multicast


def _normalise(host: str) -> str:
    return host.strip().rstrip(".").lower()


@dataclass(frozen=True)
class EgressPolicy:
    allowed_internal_hosts: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_hosts(cls, hosts: Iterable[str]) -> EgressPolicy:
        return cls(frozenset(_normalise(h) for h in hosts if h.strip()))

    def is_allow_listed(self, host: str) -> bool:
        return _normalise(host) in self.allowed_internal_hosts

    def permits(self, host: str, addresses: Sequence[IPAddress]) -> bool:
        """Connect-time decision over the addresses `host` resolved to."""
        if self.is_allow_listed(host):
            return True
        return bool(addresses) and all(is_public_address(a) for a in addresses)

    def permits_literal(self, host: str) -> bool:
        """Registration-time check: refuse a blocked IP literal immediately.

        A hostname is not resolved here -- its answer can change -- so it is judged at
        connect time by `permits`. This only stops the obvious case early.
        """
        try:
            address = ipaddress.ip_address(host.strip())
        except ValueError:
            return True
        return self.permits(host, [address])
