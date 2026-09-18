"""Section 14 rules as pure functions: manifests, verification, and the invocation decision."""

from __future__ import annotations

import uuid

import pytest

from mcp_gateway.core.config import Settings
from mcp_gateway.domain.policies.tool_policy import (
    DiscoveredTool,
    GrantRef,
    default_policy,
    denial,
    is_grantable,
    manifest_problems,
    verification_problems,
)

USER = uuid.uuid4()


def test_default_policies_and_grantability() -> None:
    assert {c: default_policy(c) for c in ("read_metadata", "read_data", "external_read")} == {
        "read_metadata": "require_grant",
        "read_data": "require_grant",
        "external_read": "require_grant",
    }
    assert default_policy("write") == default_policy("admin") == "deny"
    assert is_grantable("external_read") and not is_grantable("write")
    assert not is_grantable("admin")


def test_manifest_problems() -> None:
    assert manifest_problems([("search_docs", "read_metadata")]) == []
    assert manifest_problems([]) == ["declare at least one tool"]
    assert manifest_problems([("a b", "read_data"), ("x", "execute"), ("x", "read_data")]) == [
        "'a b': invalid tool name",
        "x: unknown tool class",
        "x: declared twice",
    ]
    assert manifest_problems([(f"t{i}", "read_data") for i in range(101)]) == ["at most 100 tools"]


def test_verification_only_ever_tightens() -> None:
    live = [
        DiscoveredTool("search_docs", read_only_hint=True),
        DiscoveredTool("delete_order", read_only_hint=False),
        DiscoveredTool("quiet_tool", read_only_hint=None),
        DiscoveredTool("undeclared", read_only_hint=False),
    ]
    assert (
        verification_problems({"search_docs": "read_metadata", "quiet_tool": "read_data"}, live)
        == []
    )
    assert verification_problems({"delete_order": "write"}, live) == []
    assert verification_problems(
        {"delete_order": "read_data", "missing": "read_metadata"}, live
    ) == [
        "delete_order: the server marks it as not read-only",
        "missing: not offered by the server",
    ]


@pytest.mark.parametrize(
    ("status", "policy", "grants", "roles", "expected"),
    [
        ("pending_approval", "require_grant", [GrantRef(None, USER)], set(), "server_not_approved"),
        ("disabled", "require_grant", [GrantRef(None, USER)], set(), "server_not_approved"),
        ("approved", "deny", [GrantRef(None, USER)], set(), "tool_denied_by_policy"),
        ("approved", "allow", [], set(), "tool_denied_by_policy"),  # reserved: not honoured
        ("approved", "require_grant", [], {"developer"}, "not_granted"),
        ("approved", "require_grant", [GrantRef(None, uuid.uuid4())], set(), "not_granted"),
        ("approved", "require_grant", [GrantRef("client", None)], {"developer"}, "not_granted"),
        ("approved", "require_grant", [GrantRef(None, USER)], set(), None),
        ("approved", "require_grant", [GrantRef("developer", None)], {"developer"}, None),
    ],
)
def test_denial_order(
    status: str, policy: str, grants: list[GrantRef], roles: set[str], expected: str | None
) -> None:
    assert (
        denial(
            server_status=status,
            policy=policy,
            grants=grants,
            user_id=USER,
            roles=frozenset(roles),
        )
        == expected
    )


def test_production_refuses_laptop_settings() -> None:
    Settings(
        environment="dev", egress_allowed_internal_hosts=["localhost"]
    ).assert_production_safe()
    with pytest.raises(RuntimeError) as info:
        Settings(
            environment="prod", egress_allowed_internal_hosts=["LOCALHOST"]
        ).assert_production_safe()
    message = str(info.value)
    for problem in ("service_client_secret", "require_gateway_token", "vault_token", "loopback"):
        assert problem in message
