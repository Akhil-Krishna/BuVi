"""Tenant policies and their effect on a principal (Sections 2, 6.6, 7.1; Phase A10).

Section 7.1 leaves three cells to the tenant, and all of them resolve here:

* `dashboard:share` for `client` is "tenant policy";
* `mcp:manage` for `developer` is "(if granted)", granted tenant-wide, since per-user
  permission lists would be a second authorization model to audit;
* WebAuthn "SHOULD be required (tenant-configurable policy) for `org_admin`" (Section 6.6).

Pure functions. Every place that builds a `Principal` (a session, an API key's owner, delegated
resolution) calls `effective_permissions` and `webauthn_required`, so a policy means the same
thing everywhere, and the other services never read policies themselves.
"""

from __future__ import annotations

from dataclasses import dataclass

from platform_auth.permissions import (
    PERM_DASHBOARD_SHARE,
    PERM_MCP_MANAGE,
    PERM_PLATFORM_ALL,
    ROLE_CLIENT,
    ROLE_DEVELOPER,
    ROLE_ORG_ADMIN,
    ROLE_PLATFORM_SUPER_ADMIN,
    permissions_for_roles,
)


@dataclass(frozen=True)
class Policies:
    """A tenant's policies. The defaults are the most restrictive setting."""

    client_can_share_dashboards: bool = False
    developer_can_manage_mcp: bool = False
    org_admin_requires_webauthn: bool = False


DEFAULT_POLICIES = Policies()


def effective_permissions(roles: frozenset[str], policies: Policies) -> frozenset[str]:
    """The Section 7.1 matrix for `roles`, plus what the tenant's policies grant."""
    permissions = set(permissions_for_roles(roles))
    if policies.client_can_share_dashboards and ROLE_CLIENT in roles:
        permissions.add(PERM_DASHBOARD_SHARE)
    if policies.developer_can_manage_mcp and ROLE_DEVELOPER in roles:
        permissions.add(PERM_MCP_MANAGE)
    return frozenset(permissions)


def webauthn_required(
    roles: frozenset[str], policies: Policies, permissions: frozenset[str]
) -> bool:
    """Whether this caller's step-up must be WebAuthn (Section 6.6)."""
    if ROLE_PLATFORM_SUPER_ADMIN in roles or PERM_PLATFORM_ALL in permissions:
        return True
    return policies.org_admin_requires_webauthn and ROLE_ORG_ADMIN in roles
