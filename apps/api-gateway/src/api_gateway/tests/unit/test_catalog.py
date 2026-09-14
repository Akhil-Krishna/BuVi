"""The gateway catalog against Section 9 of the spec -- parsed from the spec itself.

If a route is added to or removed from Section 9 without the gateway following, or
the gateway grows a route the spec does not list, this fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from api_gateway.domain.catalog import CATALOG
from api_gateway.domain.policies.rate_limit import BucketRule, RateLimitPolicy
from platform_auth.permissions import ALL_PERMISSIONS, TENANT_ROLES

pytestmark = pytest.mark.unit

SPEC = (
    Path(__file__).resolve().parents[6]
    / "docs"
    / "architecture"
    / "Agentic_BI_Platform_Build_Spec.md"
)
_OPERATION = re.compile(r"`([A-Z/]+) (/[^`]+)`")


def _section_9() -> dict[tuple[str, str], bool]:
    text = SPEC.read_text()
    block = text[text.index("## 9. API contract catalog") : text.index("### 9.1")]
    routes: dict[tuple[str, str], bool] = {}
    for line in block.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        match = _OPERATION.search(cells[1])
        if not match:
            continue
        public = "(public" in cells[1]
        for method in match.group(1).split("/"):
            routes[(method, match.group(2))] = public
    return routes


def test_spec_table_was_parsed() -> None:
    assert len(_section_9()) >= 40


def test_every_section_9_route_is_in_the_catalog_and_nothing_extra() -> None:
    spec = set(_section_9())
    catalog = {(r.method, r.path) for r in CATALOG if r.in_section_9}
    assert spec - catalog == set(), "Section 9 routes missing from the gateway"
    assert catalog - spec == set(), "gateway routes flagged Section 9 that the spec does not list"


def test_public_flags_match_the_spec() -> None:
    spec = _section_9()
    for route in CATALOG:
        if route.in_section_9:
            assert route.public is spec[(route.method, route.path)], f"{route.method} {route.path}"


def test_catalog_has_no_duplicate_operations() -> None:
    keys = [(r.method, r.path) for r in CATALOG]
    assert len(keys) == len(set(keys))


def test_permissions_and_roles_exist_in_section_7() -> None:
    for route in CATALOG:
        for permission in {route.permission, *route.any_permission} - {None}:
            assert permission in ALL_PERMISSIONS, f"{route.path}: {permission}"
        if route.role:
            assert route.role in TENANT_ROLES


def test_public_routes_carry_no_authorization_requirements() -> None:
    for route in (r for r in CATALOG if r.public):
        assert not (route.permission or route.any_permission or route.role or route.step_up)


def test_login_shaped_routes_use_the_strict_auth_tier() -> None:
    """Section 24: auth endpoints get limits independent of the general limiter."""
    strict = {(r.method, r.path) for r in CATALOG if r.rate_tier == "auth"}
    assert {
        ("GET", "/auth/login"),
        ("GET", "/auth/callback"),
        ("POST", "/auth/mfa/verify"),
        ("POST", "/invitations/{token}/accept"),
    } <= strict


def test_rate_limit_buckets_are_scoped_per_ip_user_and_tenant() -> None:
    policy = RateLimitPolicy(
        auth_ip=BucketRule("auth", "ip", 1, 1.0),
        public_ip=BucketRule("public", "ip", 1, 1.0),
        user=BucketRule("user", "user", 1, 1.0),
        tenant=BucketRule("tenant", "tenant", 1, 1.0),
    )
    assert [b.key for b in policy.before_auth(rate_tier="auth", client_ip="1.2.3.4")] == [
        "rl:auth:ip:1.2.3.4"
    ]
    assert [b.key for b in policy.before_auth(rate_tier="authenticated", client_ip="1.2.3.4")] == [
        "rl:public:ip:1.2.3.4"
    ]
    assert [b.key for b in policy.after_auth(tenant_id="t", user_id="u")] == [
        "rl:user:t:u",
        "rl:tenant:t",
    ]
