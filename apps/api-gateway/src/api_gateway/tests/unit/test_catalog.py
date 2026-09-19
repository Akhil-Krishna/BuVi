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
        authenticated_ip=BucketRule("authenticated", "ip", 1, 1.0),
        user=BucketRule("user", "user", 1, 1.0),
        tenant=BucketRule("tenant", "tenant", 1, 1.0),
    )
    assert [b.key for b in policy.before_auth(rate_tier="auth", client_ip="1.2.3.4")] == [
        "rl:auth:ip:1.2.3.4"
    ]
    assert [b.key for b in policy.before_auth(rate_tier="public", client_ip="1.2.3.4")] == [
        "rl:public:ip:1.2.3.4"
    ]
    # Authenticated traffic does not share the public bucket: one NAT carries a whole office.
    assert [b.key for b in policy.before_auth(rate_tier="authenticated", client_ip="1.2.3.4")] == [
        "rl:authenticated:ip:1.2.3.4"
    ]
    assert [b.key for b in policy.after_auth(tenant_id="t", user_id="u")] == [
        "rl:user:t:u",
        "rl:tenant:t",
    ]


def test_every_public_route_has_a_strict_ip_tier() -> None:
    """The generous authenticated-IP guard is only for routes that introspect a session."""
    assert all(r.rate_tier in ("auth", "public") for r in CATALOG if r.public)


#: Section 7.3's sensitive operations, as routes. The gateway refuses each without a fresh
#: step-up (Phase A10 DoD); the owning services check again behind it.
SECTION_7_3_ROUTES = {
    ("POST", "/data-sources/{id}/secret"),  # a connection's credentials
    ("POST", "/mcp/servers/{id}/approve"),  # approving an MCP server
    ("PATCH", "/admin/users/{id}/roles"),  # role changes
    ("DELETE", "/admin/users/{id}"),  # user deletion
    ("POST", "/admin/users/{id}/mfa/reset"),  # MFA on another user's account
    ("DELETE", "/me/mfa/{id}"),  # removing a factor (Section 6.6)
    ("POST", "/admin/users/{id}/sessions/revoke"),  # forced session revocation
    ("POST", "/me/api-keys"),  # creating/rotating API keys
    ("PATCH", "/admin/policies"),  # tenant policy (security posture)
    ("POST", "/admin/invitations"),  # granting access
    ("POST", "/dashboards/{id}/share-links"),  # exposing data outside a login
    ("POST", "/billing/subscription"),
    ("POST", "/admin/webhooks"),
    ("DELETE", "/admin/webhooks/{id}"),  # disabling a webhook (Phase A11)
}
#: Step-up that depends on data only the owning service sees, so it is enforced there (and
#: tested in that service): write/admin MCP tools, result reads above the export threshold,
#: and enrolling a second MFA factor.
SERVICE_ENFORCED_STEP_UP = {
    ("POST", "/mcp/servers/{id}/tools/{tool}/grants"),
    ("POST", "/mcp/servers/{id}/tools/{tool}/invoke"),
    ("POST", "/sql/execute"),
    ("GET", "/artifacts/{id}/data"),
    ("POST", "/auth/mfa/enroll"),
}


def test_every_section_7_3_operation_requires_step_up_at_the_gateway() -> None:
    routes = {(r.method, r.path): r for r in CATALOG}
    missing = {key for key in SECTION_7_3_ROUTES if not routes[key].step_up}
    assert missing == set(), f"Section 7.3 routes without step-up: {sorted(missing)}"
    assert all(key in routes for key in SERVICE_ENFORCED_STEP_UP)
    # Nothing else is step-up by accident: the list above is the whole policy.
    assert {k for k, r in routes.items() if r.step_up} == SECTION_7_3_ROUTES


def test_every_remaining_stub_is_tracked_post_ga_backlog() -> None:
    """A stub labelled with a Track A phase outlives that phase unnoticed (A11 found one), so the
    only stubs allowed are post-GA ones the spec's backlog names."""
    spec = (
        Path(__file__).resolve().parents[6]
        / "docs"
        / "architecture"
        / "Agentic_BI_Platform_Build_Spec.md"
    ).read_text()
    backlog = spec.split("### Post-GA backlog", 1)[1].split("\n---\n", 1)[0]
    stubs = [route for route in CATALOG if route.is_stub]
    assert stubs, "no stubs left: drop this test's post-GA assumption"
    for route in stubs:
        assert route.available_in_phase == "post-GA", route.path
        assert f"{route.method} {route.path}" in backlog, f"{route.path} not in the post-GA backlog"
