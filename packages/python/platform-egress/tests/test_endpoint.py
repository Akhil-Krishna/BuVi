"""Section 15 at registration: what the endpoint text alone can refuse (no network)."""

from __future__ import annotations

import pytest

from platform_egress import EgressPolicy, EndpointRejected, parse_endpoint

PUBLIC = EgressPolicy()
DEV = EgressPolicy.from_hosts(["localhost", "mcp-sample.internal"])

#: (url, reason) -- every private/loopback/link-local/metadata spelling, plus URL tricks.
REFUSED = [
    ("https://127.0.0.1/mcp", "destination_not_allowed"),
    ("https://127.1.2.3/mcp", "destination_not_allowed"),
    ("https://10.0.0.8/mcp", "destination_not_allowed"),
    ("https://172.16.4.4/mcp", "destination_not_allowed"),
    ("https://192.168.1.10/mcp", "destination_not_allowed"),
    ("https://169.254.169.254/latest/meta-data", "destination_not_allowed"),
    ("https://100.64.0.1/mcp", "destination_not_allowed"),
    ("https://0.0.0.0/mcp", "destination_not_allowed"),
    ("https://224.0.0.1/mcp", "destination_not_allowed"),
    ("https://[::1]/mcp", "destination_not_allowed"),
    ("https://[fd00::1]/mcp", "destination_not_allowed"),
    ("https://[fe80::1]/mcp", "destination_not_allowed"),
    ("https://[::ffff:127.0.0.1]/mcp", "destination_not_allowed"),
    ("https://[::ffff:169.254.169.254]/mcp", "destination_not_allowed"),
    ("https://[2002:7f00:1::]/mcp", "destination_not_allowed"),  # 6to4 of 127.0.0.1
    ("https://2130706433/mcp", "numeric_host"),  # 127.0.0.1 as one integer
    ("https://0x7f000001/mcp", "numeric_host"),
    ("https://0177.0.0.1/mcp", "numeric_host"),  # octal
    ("https://0x7f.1/mcp", "numeric_host"),
    ("https://127.1/mcp", "numeric_host"),
    ("http://mcp.example.com/mcp", "https_required"),
    ("http://127.0.0.1/mcp", "destination_not_allowed"),
    ("file:///etc/passwd", "scheme_not_allowed"),
    ("gopher://mcp.example.com/", "scheme_not_allowed"),
    ("ftp://mcp.example.com/", "scheme_not_allowed"),
    ("javascript:alert(1)", "scheme_not_allowed"),
    ("https://user:pass@mcp.example.com/mcp", "credentials_in_url"),
    ("https://mcp.example.com@169.254.169.254/mcp", "credentials_in_url"),
    ("https://mcp.example.com/mcp?token=abc", "query_or_fragment"),
    ("https://mcp.example.com/mcp#frag", "query_or_fragment"),
    ("https://mcp.example.com/mcp?", "query_or_fragment"),
    ("https://mcp.example.com/../admin", "malformed"),
    ("https://mcp.example.com/a b", "malformed"),
    ("https://mcp.example.com/\r\nHost: evil", "malformed"),
    ("https://mcp.example.com:99999/mcp", "malformed"),
    ("https://[fe80::1%25eth0]/mcp", "destination_not_allowed"),  # zone ids parse as scoped IPv6
    ("https://exa_mple.com/mcp", "invalid_host"),
    ("https://-bad.example.com/mcp", "invalid_host"),
    ("https:///mcp", "malformed"),
    ("", "malformed"),
    ("https://" + "a" * 2100 + ".com/", "malformed"),
]


@pytest.mark.parametrize(("url", "reason"), REFUSED)
def test_refused_endpoints(url: str, reason: str) -> None:
    with pytest.raises(EndpointRejected) as info:
        parse_endpoint(url, PUBLIC)
    assert info.value.reason == reason


def test_accepted_endpoints_are_normalized() -> None:
    endpoint = parse_endpoint("HTTPS://MCP.Example.com./v1/mcp", PUBLIC)
    assert (endpoint.scheme, endpoint.host, endpoint.port, endpoint.path) == (
        "https",
        "mcp.example.com",
        443,
        "/v1/mcp",
    )
    assert endpoint.url == "https://mcp.example.com/v1/mcp"
    assert parse_endpoint("https://mcp.example.com:8443", PUBLIC).url == (
        "https://mcp.example.com:8443/"
    )
    assert parse_endpoint("https://93.184.215.14/mcp", PUBLIC).host == "93.184.215.14"
    assert parse_endpoint("https://[2606:2800:220:1::]/mcp", PUBLIC).url == (
        "https://[2606:2800:220:1::]/mcp"
    )


def test_plain_http_only_for_allow_listed_internal_hosts() -> None:
    assert parse_endpoint("http://localhost:8765/mcp", DEV).url == "http://localhost:8765/mcp"
    assert parse_endpoint("http://mcp-sample.internal/mcp", DEV).port == 80
    with pytest.raises(EndpointRejected):
        parse_endpoint("http://localhost:8765/mcp", PUBLIC)
