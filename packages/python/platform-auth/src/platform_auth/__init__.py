"""Principal, OIDC/JWT validation, and authorization dependencies (Sections 6.3, 7.2).

Contract: `Principal`, `require_permission`, `require_resource_owner`, `require_step_up`,
`StepUpRequiredError`,
service tokens, and `IntrospectionClient`. A permission check alone is never sufficient on
an endpoint that takes a resource ID -- cross-tenant IDs return 404, not 403 (Section 7.2).

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
from platform_auth.introspection import (
    CredentialRejectedError,
    IdentityTimeoutError,
    IntrospectionClient,
    IntrospectionError,
    PrincipalNotActiveError,
    request_credentials,
)
from platform_auth.permissions import (
    ALL_PERMISSIONS,
    DEMO_ROLES,
    ROLE_PERMISSIONS,
    TENANT_ROLES,
    permissions_for_roles,
)
from platform_auth.principal import STEP_UP_MAX_AGE, AuthMethod, Principal
from platform_auth.service_tokens import (
    SERVICE_AUTH_HEADER,
    ServiceIdentity,
    ServiceTokenClient,
    ServiceTokenError,
    ServiceTokenIssuer,
    ServiceTokenVerifier,
    install_service_token_verifier,
    require_service_scope,
    verify_service_request,
)
from platform_auth.step_up import StepUpRequiredError

__all__ = [
    "ALL_PERMISSIONS",
    "DEMO_ROLES",
    "ROLE_PERMISSIONS",
    "SERVICE_AUTH_HEADER",
    "STEP_UP_MAX_AGE",
    "TENANT_ROLES",
    "AuthMethod",
    "CredentialRejectedError",
    "IdentityTimeoutError",
    "IntrospectionClient",
    "IntrospectionError",
    "Principal",
    "PrincipalNotActiveError",
    "PrincipalResolver",
    "ServiceIdentity",
    "ServiceTokenClient",
    "ServiceTokenError",
    "ServiceTokenIssuer",
    "ServiceTokenVerifier",
    "StepUpRequiredError",
    "TenantLoader",
    "get_principal",
    "install_principal_resolver",
    "install_service_token_verifier",
    "permissions_for_roles",
    "request_credentials",
    "require_permission",
    "require_resource_owner",
    "require_service_scope",
    "require_step_up",
    "verify_service_request",
]
