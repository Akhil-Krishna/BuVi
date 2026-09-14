"""Role-assignment rules (Section 2).

Pure functions over plain data: no database, no FastAPI, no I/O. That keeps them
exhaustively unit-testable, which matters because these are the rules an
authorization bug would slip past.
"""

from __future__ import annotations

from dataclasses import dataclass

from identity_service.domain.errors import (
    LastOrgAdminError,
    SelfServiceForbiddenError,
    UnknownRoleError,
)
from platform_auth.permissions import ROLE_ORG_ADMIN, TENANT_ROLES


@dataclass(frozen=True)
class RoleChange:
    """A requested grant/revoke set for one user."""

    grant: frozenset[str]
    revoke: frozenset[str]


def validate_role_keys(keys: frozenset[str]) -> None:
    """Reject any role outside the Section 2 tenant role list.

    Section 37: no role beyond the ones listed in Section 2 without an ADR. A
    typo'd role key must fail loudly rather than silently granting nothing.
    """
    unknown = keys - TENANT_ROLES
    if unknown:
        raise UnknownRoleError("One or more roles are not recognized.", roles=sorted(unknown))


def apply_role_change(current: frozenset[str], change: RoleChange) -> frozenset[str]:
    """Compute the resulting role set. Revocation loses ties with a grant."""
    validate_role_keys(change.grant | change.revoke)
    return (current | change.grant) - change.revoke


def assert_tenant_keeps_an_org_admin(
    *,
    org_admin_count: int,
    target_is_org_admin: bool,
    target_remains_org_admin: bool,
) -> None:
    """Section 2: a tenant MUST retain at least one `org_admin`.

    `org_admin_count` counts *active* org admins in the tenant, including the
    target. Demoting or deleting the last one is refused.
    """
    if not target_is_org_admin or target_remains_org_admin:
        return
    if org_admin_count <= 1:
        raise LastOrgAdminError()


def assert_not_self_target(*, actor_user_id: str, target_user_id: str, operation: str) -> None:
    """Refuse admin operations an actor may not aim at their own account.

    Self-deletion and self-demotion are the two ways a tenant loses its last
    administrator without any single check noticing, so they are refused
    outright rather than relying on the count above.
    """
    if actor_user_id == target_user_id:
        raise SelfServiceForbiddenError(
            f"You cannot {operation} your own account.", operation=operation
        )


def default_role_for_jit_provisioning() -> str:
    """Section 6.4: a first federated login lands on `client`, not on admin."""
    return "client"


__all__ = [
    "ROLE_ORG_ADMIN",
    "RoleChange",
    "apply_role_change",
    "assert_not_self_target",
    "assert_tenant_keeps_an_org_admin",
    "default_role_for_jit_provisioning",
    "validate_role_keys",
]
