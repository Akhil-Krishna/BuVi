"""Connect-time DNS resolution (Section 15).

Resolve once, validate the answers with `EgressPolicy`, then connect to exactly those
addresses: a DNS answer that changes between check and connect (rebinding) cannot redirect
the connection.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket

from platform_egress.policy import IPAddress


class HostResolutionError(Exception):
    """The host did not resolve. Carries no detail: the host is tenant-supplied."""


async def resolve_host(host: str, port: int, *, timeout_seconds: float = 5.0) -> list[IPAddress]:
    """Unique addresses for `host`, IPv4 first. An IP literal resolves to itself."""
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=timeout_seconds
        )
    except (OSError, TimeoutError):
        raise HostResolutionError() from None
    unique: dict[str, IPAddress] = {}
    for _family, _type, _proto, _canon, sockaddr in infos:
        try:
            address = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        except ValueError:
            continue
        unique.setdefault(str(address), address)
    if not unique:
        raise HostResolutionError()
    return sorted(unique.values(), key=lambda a: a.version)
