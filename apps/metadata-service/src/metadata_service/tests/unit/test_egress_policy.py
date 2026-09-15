"""Section 15 egress policy: which destinations a data-source connection may reach."""

from __future__ import annotations

import ipaddress

import pytest

from metadata_service.domain.policies.egress import EgressPolicy, is_public_address

pytestmark = [pytest.mark.unit, pytest.mark.security]

BLOCKED = [
    "127.0.0.1",  # loopback
    "10.1.2.3",  # RFC 1918
    "172.16.0.10",
    "192.168.1.1",
    "169.254.169.254",  # link-local: cloud metadata endpoint
    "100.64.0.1",  # CGNAT shared space
    "0.0.0.0",  # noqa: S104 - unspecified address under test
    "224.0.0.1",  # multicast
    "240.0.0.1",  # reserved
    "::1",
    "fc00::1",  # RFC 4193 unique local
    "fe80::1",  # link-local
    "::ffff:127.0.0.1",  # IPv4-mapped loopback
    "::ffff:10.0.0.1",
    "2002:a00:1::",  # 6to4 wrapping 10.0.0.1
]
PUBLIC = ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"]


@pytest.mark.parametrize("address", BLOCKED)
def test_non_public_addresses_are_blocked(address: str) -> None:
    assert not is_public_address(ipaddress.ip_address(address))
    assert not EgressPolicy().permits("db.example.com", [ipaddress.ip_address(address)])


@pytest.mark.parametrize("address", PUBLIC)
def test_public_addresses_are_permitted(address: str) -> None:
    assert is_public_address(ipaddress.ip_address(address))


def test_one_private_answer_among_public_ones_blocks_the_host() -> None:
    answers = [ipaddress.ip_address("8.8.8.8"), ipaddress.ip_address("10.0.0.5")]
    assert not EgressPolicy().permits("rebind.example.com", answers)


def test_no_answers_is_not_permitted() -> None:
    assert not EgressPolicy().permits("db.example.com", [])


def test_allow_listed_internal_host_is_exempt_after_normalisation() -> None:
    policy = EgressPolicy.from_hosts([" Sample-Sales-DB ", ""])
    loopback = [ipaddress.ip_address("172.18.0.4")]
    assert policy.permits("sample-sales-db.", loopback)
    assert policy.permits("SAMPLE-SALES-DB", loopback)
    assert not policy.permits("other-db", loopback)


def test_registration_time_check_only_judges_ip_literals() -> None:
    policy = EgressPolicy()
    assert not policy.permits_literal("127.0.0.1")
    assert not policy.permits_literal("169.254.169.254")
    assert policy.permits_literal("8.8.8.8")
    # A hostname is judged when it is resolved, at connect time.
    assert policy.permits_literal("localhost")
    assert EgressPolicy.from_hosts(["127.0.0.1"]).permits_literal("127.0.0.1")
