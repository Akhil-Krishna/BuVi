"""DNS resolution for outbound connections (Section 15).

Resolution happens once, at connect time; the connector validates these addresses
with `EgressPolicy` and then connects to exactly them, so a DNS answer that changes
between "check" and "connect" (rebinding) cannot redirect the connection.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable

from metadata_service.domain.policies.egress import IPAddress
from metadata_service.domain.value_objects.diagnostics import ConnectorError, DiagnosticCode

HostResolver = Callable[[str, int], Awaitable[list[IPAddress]]]

_RESOLVE_TIMEOUT_SECONDS = 5.0


async def resolve_host(host: str, port: int) -> list[IPAddress]:
    """Resolve `host` to unique addresses, IPv4 first. Unresolvable hosts are unreachable."""
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM),
            timeout=_RESOLVE_TIMEOUT_SECONDS,
        )
    except (OSError, TimeoutError):
        raise ConnectorError(DiagnosticCode.HOST_UNREACHABLE) from None
    unique: dict[str, IPAddress] = {}
    for _family, _type, _proto, _canon, sockaddr in infos:
        try:
            address = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        except ValueError:
            continue
        unique.setdefault(str(address), address)
    if not unique:
        raise ConnectorError(DiagnosticCode.HOST_UNREACHABLE)
    return sorted(unique.values(), key=lambda a: a.version)
