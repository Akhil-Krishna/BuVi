"""The Section 7.1 permission matrix, asserted cell by cell.

This is the table an authorization bug hides in, so it is transcribed here a
second time, independently of the implementation, and compared. A change to
`platform_auth.permissions` that is not also a change to Section 7.1 fails here.
"""

from __future__ import annotations

import pytest

from platform_auth.permissions import (
    ALL_PERMISSIONS,
    DEMO_ROLES,
    PERM_PLATFORM_ALL,
    ROLE_AUDITOR,
    ROLE_BILLING_ADMIN,
    ROLE_CLIENT,
    ROLE_DEVELOPER,
    ROLE_ORG_ADMIN,
    ROLE_PERMISSIONS,
    ROLE_PLATFORM_SUPER_ADMIN,
    ROLE_SERVICE_ACCOUNT,
    permissions_for_roles,
)

# Section 7.1, transcribed as a table so it can be read against the spec at a
# glance. "Y" = the spec ticks the cell outright. Cells the spec qualifies
# ("tenant policy", "if granted", "partial") are "-": they are not defaults of
# the role, they are grants applied per resource elsewhere.
#
#                     client  developer  org_admin  billing  auditor
_TABLE = """
chat:use                Y         Y          Y         -        -
dashboard:read          Y         Y          Y         -        Y
dashboard:pin           Y         Y          Y         -        -
dashboard:share         -         Y          Y         -        -
artifact:read           Y         Y          Y         -        Y
sql:execute             -         Y          Y         -        -
data:manage             -         Y          Y         -        -
catalog:read            -         Y          Y         -        Y
semantic:manage         -         Y          Y         -        -
mcp:manage              -         -          Y         -        -
run:debug               -         Y          Y         -        Y
user:manage             -         -          Y         -        -
role:manage             -         -          Y         -        -
policy:manage           -         -          Y         -        -
billing:read            -         -          Y         Y        -
billing:manage          -         -          Y         Y        -
audit:read              -         -          Y         -        Y
"""

_COLUMNS = [ROLE_CLIENT, ROLE_DEVELOPER, ROLE_ORG_ADMIN, ROLE_BILLING_ADMIN, ROLE_AUDITOR]

MATRIX: dict[str, dict[str, bool]] = {
    row.split()[0]: dict(zip(_COLUMNS, [cell == "Y" for cell in row.split()[1:]], strict=True))
    for row in _TABLE.strip().splitlines()
}

ROLES = _COLUMNS


@pytest.mark.unit
@pytest.mark.parametrize("permission", sorted(MATRIX))
@pytest.mark.parametrize("role", ROLES)
def test_matrix_cell(role: str, permission: str) -> None:
    granted = permission in ROLE_PERMISSIONS[role]
    assert granted is MATRIX[permission][role], (
        f"Section 7.1 says {role} {'has' if MATRIX[permission][role] else 'does not have'} "
        f"{permission}, but the matrix says otherwise"
    )


@pytest.mark.unit
def test_matrix_covers_every_permission() -> None:
    """No permission may exist that Section 7.1 does not list."""
    assert set(MATRIX) == set(ALL_PERMISSIONS)


@pytest.mark.unit
def test_platform_super_admin_holds_only_the_wildcard() -> None:
    """Section 7.1: excluded from the per-tenant table by design."""
    assert ROLE_PERMISSIONS[ROLE_PLATFORM_SUPER_ADMIN] == frozenset({PERM_PLATFORM_ALL})


@pytest.mark.unit
def test_service_account_starts_with_nothing() -> None:
    """Section 7.1: an explicit allow-list, never derived from a role."""
    assert ROLE_PERMISSIONS[ROLE_SERVICE_ACCOUNT] == frozenset()


@pytest.mark.unit
def test_client_cannot_execute_sql() -> None:
    """The single most load-bearing negative in the matrix."""
    assert "sql:execute" not in permissions_for_roles({ROLE_CLIENT})


@pytest.mark.unit
def test_unknown_role_contributes_nothing() -> None:
    """A removed or misspelled role must fail closed, not raise."""
    assert permissions_for_roles({"not_a_role"}) == frozenset()


@pytest.mark.unit
def test_roles_union() -> None:
    combined = permissions_for_roles({ROLE_CLIENT, ROLE_BILLING_ADMIN})
    assert "chat:use" in combined
    assert "billing:read" in combined
    assert "user:manage" not in combined


@pytest.mark.unit
def test_demo_roles_are_the_three_product_roles() -> None:
    assert set(DEMO_ROLES) == {ROLE_CLIENT, ROLE_DEVELOPER, ROLE_ORG_ADMIN}
