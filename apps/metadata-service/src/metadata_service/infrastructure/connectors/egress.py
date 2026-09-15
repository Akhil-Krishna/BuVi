"""Connect-time resolution for data-source connections (Section 15).

`platform_egress.resolve_host`, mapped onto this service's diagnostic codes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from metadata_service.domain.value_objects.diagnostics import ConnectorError, DiagnosticCode
from platform_egress import HostResolutionError, IPAddress
from platform_egress import resolve_host as _resolve_host

HostResolver = Callable[[str, int], Awaitable[list[IPAddress]]]


async def resolve_host(host: str, port: int) -> list[IPAddress]:
    try:
        return await _resolve_host(host, port)
    except HostResolutionError:
        raise ConnectorError(DiagnosticCode.HOST_UNREACHABLE) from None
