"""Principal, OIDC/JWT validation, and authorization dependencies (Sections 6.3, 7.2).

Contract: `Principal`, `require_permission`, `require_resource_owner`, and
`require_step_up`. A permission check alone is never sufficient on an endpoint
that takes a resource ID -- cross-tenant IDs return 404, not 403 (Section 7.2).

Role-to-permission resolution is the Section 7.1 matrix in `permissions`; no
service re-derives it and none reads a permission list out of a token claim.
"""

from platform_auth.dependencies import (
    PrincipalResolver,
    TenantLoader,
    get_principal,
    install_principal_resolver,
    require_permission,
    require_resource_owner,
    require_step_up,
)
from platform_auth.permissions import (
    ALL_PERMISSIONS,
    DEMO_ROLES,
    ROLE_PERMISSIONS,
    TENANT_ROLES,
    permissions_for_roles,
)
from platform_auth.principal import STEP_UP_MAX_AGE, AuthMethod, Principal

__all__ = [
    "ALL_PERMISSIONS",
    "DEMO_ROLES",
    "ROLE_PERMISSIONS",
    "STEP_UP_MAX_AGE",
    "TENANT_ROLES",
    "AuthMethod",
    "Principal",
    "PrincipalResolver",
    "TenantLoader",
    "get_principal",
    "install_principal_resolver",
    "permissions_for_roles",
    "require_permission",
    "require_resource_owner",
    "require_step_up",
]
