"""Connect-time resolution."""

from __future__ import annotations

import ipaddress

import pytest

from platform_egress import HostResolutionError, resolve_host

pytestmark = pytest.mark.unit


async def test_ip_literal_resolves_to_itself() -> None:
    assert await resolve_host("203.0.113.5", 5432) == [ipaddress.ip_address("203.0.113.5")]


async def test_localhost_resolves_to_loopback_ipv4_first() -> None:
    addresses = await resolve_host("localhost", 5432)
    assert addresses and all(a.is_loopback for a in addresses)
    assert addresses == sorted(addresses, key=lambda a: a.version)


async def test_unresolvable_host_raises_without_detail() -> None:
    with pytest.raises(HostResolutionError) as info:
        await resolve_host("no-such-host.invalid", 5432)
    assert str(info.value) == ""
