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
    manifest_problems,
    needs_step_up,
    verification_problems,
)

USER = uuid.uuid4()


def test_every_class_needs_a_grant_and_write_admin_need_step_up() -> None:
    classes = ("read_metadata", "read_data", "external_read", "write", "admin")
    assert {c: default_policy(c) for c in classes} == dict.fromkeys(classes, "require_grant")
    assert [c for c in classes if needs_step_up(c)] == ["write", "admin"]


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


GRANTED = [GrantRef(None, USER)]


@pytest.mark.parametrize(
    ("status", "tool_class", "policy", "grants", "roles", "fresh", "expected"),
    [
        (
            "pending_approval",
            "read_data",
            "require_grant",
            GRANTED,
            set(),
            True,
            "server_not_approved",
        ),
        ("disabled", "read_data", "require_grant", GRANTED, set(), True, "server_not_approved"),
        ("rejected", "read_data", "require_grant", GRANTED, set(), True, "server_not_approved"),
        ("approved", "read_data", "deny", GRANTED, set(), True, "tool_denied_by_policy"),
        ("approved", "read_data", "allow", [], set(), True, "tool_denied_by_policy"),  # reserved
        ("approved", "read_data", "require_grant", [], {"developer"}, True, "not_granted"),
        (
            "approved",
            "read_data",
            "require_grant",
            [GrantRef(None, uuid.uuid4())],
            set(),
            True,
            "not_granted",
        ),
        (
            "approved",
            "read_data",
            "require_grant",
            [GrantRef("client", None)],
            {"developer"},
            True,
            "not_granted",
        ),
        (
            "approved",
            "read_data",
            "require_grant",
            GRANTED,
            set(),
            False,
            None,
        ),  # reads: no step-up
        (
            "approved",
            "read_data",
            "require_grant",
            [GrantRef("developer", None)],
            {"developer"},
            False,
            None,
        ),
        # Grant first: a caller who could never invoke is not asked for MFA.
        ("approved", "write", "require_grant", [], {"developer"}, False, "not_granted"),
        ("approved", "write", "require_grant", GRANTED, {"developer"}, False, "step_up_required"),
        ("approved", "write", "require_grant", GRANTED, {"developer"}, True, None),
        ("approved", "admin", "require_grant", GRANTED, {"developer"}, True, "admin_only"),
        ("approved", "admin", "require_grant", GRANTED, {"org_admin"}, False, "step_up_required"),
        ("approved", "admin", "require_grant", GRANTED, {"org_admin"}, True, None),
    ],
)
def test_denial_order(
    status: str,
    tool_class: str,
    policy: str,
    grants: list[GrantRef],
    roles: set[str],
    fresh: bool,
    expected: str | None,
) -> None:
    assert (
        denial(
            server_status=status,
            tool_class=tool_class,
            policy=policy,
            grants=grants,
            user_id=USER,
            roles=frozenset(roles),
            step_up_fresh=fresh,
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
