"""The Section 7.1 permission matrix, as executable data.

This module is the single authoritative mapping from a role key to the
permission set that role carries. Services import it; none of them re-derive it,
and none of them read a permission list out of a JWT claim -- Section 2's design
rule is that a token establishes *who* the caller is, while what they may do is
resolved here, server-side, on every request.

Adding a permission or a role means editing Section 7.1 of the build spec and
recording an ADR first (Section 37: "No role beyond the ones listed in Section 2
without an ADR").
"""

from __future__ import annotations

from typing import Final

# --- Roles (Section 2) -------------------------------------------------------

ROLE_PLATFORM_SUPER_ADMIN: Final = "platform_super_admin"
ROLE_ORG_ADMIN: Final = "org_admin"
ROLE_BILLING_ADMIN: Final = "billing_admin"
ROLE_DEVELOPER: Final = "developer"
ROLE_CLIENT: Final = "client"
ROLE_AUDITOR: Final = "auditor"
ROLE_SERVICE_ACCOUNT: Final = "service_account"
ROLE_GUEST: Final = "guest"

#: Roles that may be granted to a human user inside a tenant.
TENANT_ROLES: Final[frozenset[str]] = frozenset(
    {ROLE_ORG_ADMIN, ROLE_BILLING_ADMIN, ROLE_DEVELOPER, ROLE_CLIENT, ROLE_AUDITOR}
)

#: The three first-class product roles used by the demo tenant and the Phase A1
#: scripted login flow.
DEMO_ROLES: Final[tuple[str, ...]] = (ROLE_CLIENT, ROLE_DEVELOPER, ROLE_ORG_ADMIN)

# --- Permissions (Section 7.1) -----------------------------------------------

PERM_CHAT_USE: Final = "chat:use"
PERM_DASHBOARD_READ: Final = "dashboard:read"
PERM_DASHBOARD_PIN: Final = "dashboard:pin"
PERM_DASHBOARD_SHARE: Final = "dashboard:share"
PERM_ARTIFACT_READ: Final = "artifact:read"
PERM_SQL_EXECUTE: Final = "sql:execute"
PERM_DATA_MANAGE: Final = "data:manage"
#: Read connection metadata and the schema catalog; never credentials.
PERM_CATALOG_READ: Final = "catalog:read"
PERM_SEMANTIC_MANAGE: Final = "semantic:manage"
PERM_MCP_MANAGE: Final = "mcp:manage"
PERM_RUN_DEBUG: Final = "run:debug"
PERM_USER_MANAGE: Final = "user:manage"
PERM_ROLE_MANAGE: Final = "role:manage"
PERM_POLICY_MANAGE: Final = "policy:manage"
PERM_BILLING_READ: Final = "billing:read"
PERM_BILLING_MANAGE: Final = "billing:manage"
PERM_AUDIT_READ: Final = "audit:read"

#: Wildcard held only by `platform_super_admin`; grants cross-tenant reach and is
#: the sole exemption from the resource-tenant check in Section 7.2.
PERM_PLATFORM_ALL: Final = "platform:*"

ALL_PERMISSIONS: Final[frozenset[str]] = frozenset(
    {
        PERM_CHAT_USE,
        PERM_DASHBOARD_READ,
        PERM_DASHBOARD_PIN,
        PERM_DASHBOARD_SHARE,
        PERM_ARTIFACT_READ,
        PERM_SQL_EXECUTE,
        PERM_DATA_MANAGE,
        PERM_CATALOG_READ,
        PERM_SEMANTIC_MANAGE,
        PERM_MCP_MANAGE,
        PERM_RUN_DEBUG,
        PERM_USER_MANAGE,
        PERM_ROLE_MANAGE,
        PERM_POLICY_MANAGE,
        PERM_BILLING_READ,
        PERM_BILLING_MANAGE,
        PERM_AUDIT_READ,
    }
)

# --- The matrix --------------------------------------------------------------
#
# Transcribed column-by-column from Section 7.1. Cells the spec qualifies rather
# than simply ticking are noted where they are granted:
#
#   dashboard:share for `client`  -- "tenant policy": the coarse permission is
#       withheld here and granted by tenant policy in Phase A10, so the default
#       is deny (Section 7: deny-by-default).
#   sql:execute / data:manage for `developer` -- the spec's "(per-connection
#       grant)" and "approval by admin for prod" are the *second* authorization
#       layer (Section 7.2), enforced per resource by metadata-service and
#       query-gateway in Phases A3/A4. This coarse permission is necessary, not
#       sufficient.
#   mcp:manage for `developer` -- "(if granted)": not a default of the role, so
#       it is not in the base set; Phase A9/A10 grant it per user.
#   audit:read for `developer` -- "partial (own runs)": scoping a read to the
#       caller's own runs is a resource-level filter, not the tenant-wide
#       `audit:read` permission, so it is withheld here and applied by
#       analytics-orchestrator in Phase A5.

ROLE_PERMISSIONS: Final[dict[str, frozenset[str]]] = {
    ROLE_CLIENT: frozenset(
        {
            PERM_CHAT_USE,
            PERM_DASHBOARD_READ,
            PERM_DASHBOARD_PIN,
            PERM_ARTIFACT_READ,
        }
    ),
    ROLE_DEVELOPER: frozenset(
        {
            PERM_CHAT_USE,
            PERM_DASHBOARD_READ,
            PERM_DASHBOARD_PIN,
            PERM_DASHBOARD_SHARE,
            PERM_ARTIFACT_READ,
            PERM_SQL_EXECUTE,
            PERM_DATA_MANAGE,
            PERM_CATALOG_READ,
            PERM_SEMANTIC_MANAGE,
            PERM_RUN_DEBUG,
        }
    ),
    ROLE_ORG_ADMIN: frozenset(
        {
            PERM_CHAT_USE,
            PERM_DASHBOARD_READ,
            PERM_DASHBOARD_PIN,
            PERM_DASHBOARD_SHARE,
            PERM_ARTIFACT_READ,
            PERM_SQL_EXECUTE,
            PERM_DATA_MANAGE,
            PERM_CATALOG_READ,
            PERM_SEMANTIC_MANAGE,
            PERM_MCP_MANAGE,
            PERM_RUN_DEBUG,
            PERM_USER_MANAGE,
            PERM_ROLE_MANAGE,
            PERM_POLICY_MANAGE,
            PERM_BILLING_READ,
            PERM_BILLING_MANAGE,
            PERM_AUDIT_READ,
        }
    ),
    ROLE_BILLING_ADMIN: frozenset({PERM_BILLING_READ, PERM_BILLING_MANAGE}),
    ROLE_AUDITOR: frozenset(
        {
            PERM_CATALOG_READ,
            PERM_DASHBOARD_READ,
            PERM_ARTIFACT_READ,
            PERM_RUN_DEBUG,
            PERM_AUDIT_READ,
        }
    ),
    # Cross-tenant operator role. Excluded from the per-tenant matrix by design.
    ROLE_PLATFORM_SUPER_ADMIN: frozenset({PERM_PLATFORM_ALL}),
    # A service account's permissions are an explicit allow-list chosen at
    # creation (Section 7.1), never derived from a role. It starts with nothing.
    ROLE_SERVICE_ACCOUNT: frozenset(),
    # A guest holds `dashboard:read` on exactly one dashboard via a signed link;
    # the resource scoping is the share-link token, checked in Phase A6.
    ROLE_GUEST: frozenset({PERM_DASHBOARD_READ}),
}


def permissions_for_roles(role_keys: frozenset[str] | set[str] | list[str]) -> frozenset[str]:
    """Resolve a set of role keys to the union of their permissions.

    Unknown role keys contribute nothing rather than raising: a role that was
    removed from the matrix must fail closed, not fail the request in a way that
    reveals which role keys are recognised.
    """
    resolved: set[str] = set()
    for key in role_keys:
        resolved |= ROLE_PERMISSIONS.get(key, frozenset())
    return frozenset(resolved)
