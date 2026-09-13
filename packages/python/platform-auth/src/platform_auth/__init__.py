"""Principal, OIDC/JWT validation, and authorization dependencies (Sections 6.3, 7.2).

Contract: `Principal`, `require_permission`, and `require_resource_owner`.
A permission check alone is never sufficient on an endpoint that takes a
resource ID -- cross-tenant IDs return 404, not 403 (Section 7.2).
"""
