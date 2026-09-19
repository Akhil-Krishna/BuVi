"""The public API surface: every Section 9 route, as data.

This table is the gateway's contract. Each row states who may call a route (public,
authenticated, a Section 7.1 permission, or a role), whether Section 7.3 step-up
applies, which rate-limit tier guards it, and which service owns it. Routes whose
owning service does not exist yet answer `501 NOT_IMPLEMENTED` after authentication
and authorization -- the Phase A2 Definition of Done.

The gateway's checks are coarse by design. Resource-tenant checks (Section 7.2) and
per-resource grants live in the owning service, which re-checks everything; the
gateway is never the only trust boundary (Section 6.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

RateTier = Literal["auth", "public", "authenticated"]
#: Section 9 Idempotency-Key handling: replay the stored response; record completion without
#: storing the body (one-time secrets, customer rows); or ignore the key.
IdempotencyMode = Literal["replay", "no_store", "ignore"]
MUTATING_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})

IDENTITY: Final = "identity-service"
METADATA: Final = "metadata-service"
ANALYTICS: Final = "analytics-orchestrator"
DASHBOARD: Final = "dashboard-service"
SEMANTIC: Final = "semantic-service"
MCP: Final = "mcp-gateway"
QUERY: Final = "query-gateway"


@dataclass(frozen=True)
class RouteSpec:
    method: str
    #: Path under `/api/v1`, with Section 9's parameter names.
    path: str
    summary: str
    #: Owning service, or None when the spec has not assigned one yet.
    backend: str | None
    public: bool = False
    permission: str | None = None
    #: Satisfied by holding any one of these (e.g. `sql:execute` or `run:debug`).
    any_permission: frozenset[str] = field(default_factory=frozenset)
    role: str | None = None
    step_up: bool = False
    rate_tier: RateTier = "authenticated"
    #: Phase in which the backend is built; None when it is live now.
    available_in_phase: str | None = None
    #: False for routes an owning service exposes beyond Section 9.
    in_section_9: bool = True
    #: The decision record for a route beyond Section 9.
    decision: str = "ADR 0002 item 8"
    #: Served by the gateway itself as Server-Sent Events rather than proxied (Section 11).
    stream: bool = False
    idempotency: IdempotencyMode = "replay"

    @property
    def idempotency_mode(self) -> IdempotencyMode:
        """Reads never need a key; a public route has no principal to scope one to."""
        if self.method not in MUTATING_METHODS or self.public:
            return "ignore"
        return self.idempotency

    @property
    def is_stub(self) -> bool:
        return self.available_in_phase is not None

    @property
    def operation_id(self) -> str:
        parts = [p.strip("{}") for p in self.path.strip("/").split("/")]
        return "_".join([self.method.lower(), *parts]).replace("-", "_")


def _identity(method: str, path: str, summary: str, **kwargs: object) -> RouteSpec:
    return RouteSpec(method, path, summary, IDENTITY, **kwargs)  # type: ignore[arg-type]


def _metadata(method: str, path: str, summary: str, **kwargs: object) -> RouteSpec:
    return RouteSpec(method, path, summary, METADATA, **kwargs)  # type: ignore[arg-type]


def _analytics(method: str, path: str, summary: str, **kwargs: object) -> RouteSpec:
    return RouteSpec(method, path, summary, ANALYTICS, **kwargs)  # type: ignore[arg-type]


def _dashboard(method: str, path: str, summary: str, **kwargs: object) -> RouteSpec:
    return RouteSpec(method, path, summary, DASHBOARD, **kwargs)  # type: ignore[arg-type]


def _semantic(method: str, path: str, summary: str, **kwargs: object) -> RouteSpec:
    return RouteSpec(method, path, summary, SEMANTIC, **kwargs)  # type: ignore[arg-type]


def _query(method: str, path: str, summary: str, **kwargs: object) -> RouteSpec:
    return RouteSpec(method, path, summary, QUERY, **kwargs)  # type: ignore[arg-type]


def _mcp(method: str, path: str, summary: str, **kwargs: object) -> RouteSpec:
    return RouteSpec(method, path, summary, MCP, **kwargs)  # type: ignore[arg-type]


def _stub(
    method: str, path: str, summary: str, backend: str | None, phase: str, **kwargs: object
) -> RouteSpec:
    return RouteSpec(method, path, summary, backend, available_in_phase=phase, **kwargs)  # type: ignore[arg-type]


CATALOG: Final[tuple[RouteSpec, ...]] = (
    # --- Auth (Sections 6.1, 6.6) --------------------------------------------------
    _identity("GET", "/auth/login", "Redirect to the IdP", public=True, rate_tier="auth"),
    _identity("GET", "/auth/callback", "OIDC callback", public=True, rate_tier="auth"),
    _identity("POST", "/auth/logout", "Revoke the session and IdP token", idempotency="ignore"),
    _identity("GET", "/auth/session", "Current principal, roles, tenant"),
    _identity("POST", "/auth/mfa/enroll", "Start TOTP/WebAuthn enrollment", idempotency="no_store"),
    _identity("POST", "/auth/mfa/verify", "Complete MFA", rate_tier="auth", idempotency="ignore"),
    # Authenticated and guess-free (a challenge, not a code check): the per-user bucket covers
    # it, so a WebAuthn step-up spends one auth-tier token (verify), not two.
    _identity(
        "POST",
        "/auth/mfa/challenge",
        "WebAuthn assertion options (single-use)",
        idempotency="ignore",
    ),
    _identity("GET", "/me/mfa", "The caller's MFA factors"),
    _identity("DELETE", "/me/mfa/{id}", "Remove one of the caller's factors", step_up=True),
    # --- Users, invitations, sessions, API keys (Sections 6.7-6.9) -------------------
    _identity("GET", "/admin/users", "Tenant-scoped user list", permission="user:manage"),
    _identity(
        "POST",
        "/invitations/{token}/accept",
        "Accept an invitation via IdP login",
        public=True,
        rate_tier="auth",
    ),
    _identity("GET", "/me/sessions", "List the caller's active sessions"),
    _identity("DELETE", "/me/sessions/{id}", "Revoke one of the caller's sessions"),
    _identity(
        "POST",
        "/admin/invitations",
        "Invite a user by email and role",
        permission="user:manage",
        step_up=True,
    ),
    _identity(
        "PATCH",
        "/admin/users/{id}/roles",
        "Grant or revoke roles",
        permission="role:manage",
        step_up=True,
    ),
    _identity(
        "POST",
        "/admin/users/{id}/sessions/revoke",
        "Force logout a user",
        permission="user:manage",
        step_up=True,
    ),
    _identity(
        "DELETE",
        "/admin/users/{id}",
        "Deactivate a user",
        permission="user:manage",
        step_up=True,
    ),
    _identity(
        "POST",
        "/admin/users/{id}/mfa/reset",
        "Revoke all of a user's factors and sessions",
        permission="user:manage",
        step_up=True,
    ),
    _identity(
        "GET", "/admin/roles", "Tenant roles and their permissions", permission="role:manage"
    ),
    _identity("GET", "/admin/policies", "Tenant policies", permission="policy:manage"),
    _identity(
        "PATCH",
        "/admin/policies",
        "Change tenant policies",
        permission="policy:manage",
        step_up=True,
    ),
    _identity(
        "POST",
        "/me/api-keys",
        "Create an API key (secret shown once)",
        step_up=True,
        idempotency="no_store",
    ),
    _identity("DELETE", "/me/api-keys/{id}", "Revoke an API key"),
    _identity("GET", "/admin/audit", "Tenant audit events", permission="audit:read"),
    # identity-service routes beyond Section 9 (ADR 0002 item 8).
    _identity(
        "GET", "/admin/users/{id}", "Read one user", permission="user:manage", in_section_9=False
    ),
    _identity(
        "GET",
        "/admin/invitations",
        "List invitations",
        permission="user:manage",
        in_section_9=False,
    ),
    _identity("GET", "/me/api-keys", "List the caller's API keys", in_section_9=False),
    # --- Chat (Sections 10, 11) -------------------------------------------------------
    _analytics("POST", "/conversations", "Create a conversation", permission="chat:use"),
    _analytics(
        "POST",
        "/conversations/{id}/messages",
        "Send a message; returns run_id",
        permission="chat:use",
    ),
    _analytics(
        "GET",
        "/runs/{id}/events",
        "Run progress (SSE)",
        permission="chat:use",
        stream=True,
    ),
    _analytics("POST", "/runs/{id}/cancel", "Best-effort cancel", permission="chat:use"),
    # --- Artifacts, dashboards (Sections 16, 17) -----------------------------------------
    _dashboard("GET", "/artifacts/{id}", "Read an artifact", permission="artifact:read"),
    _dashboard(
        "GET",
        "/artifacts/{id}/data",
        "The artifact's stored result rows",
        permission="artifact:read",
    ),
    _dashboard("GET", "/dashboards", "List dashboards", permission="dashboard:read"),
    _dashboard("POST", "/dashboards", "Create a dashboard", permission="dashboard:pin"),
    _dashboard(
        "GET", "/dashboards/{id}", "A dashboard with its tiles", permission="dashboard:read"
    ),
    _dashboard(
        "POST", "/dashboards/{id}/tiles", "Pin an artifact as a tile", permission="dashboard:pin"
    ),
    _dashboard("PATCH", "/tiles/{id}", "Update tile layout/overrides", permission="dashboard:pin"),
    _dashboard(
        "POST",
        "/dashboards/{id}/share-links",
        "Create a time-boxed share link (token shown once)",
        permission="dashboard:share",
        step_up=True,
        idempotency="no_store",
    ),
    _dashboard(
        "GET",
        "/dashboards/{id}/share-links",
        "A dashboard's share links",
        permission="dashboard:read",
    ),
    _dashboard(
        "DELETE",
        "/dashboards/{id}/share-links/{link_id}",
        "Revoke a share link",
        permission="dashboard:read",
    ),
    # --- Data sources (Sections 8.2, 13.1) --------------------------------------------------
    _metadata("GET", "/data-sources", "List data sources", permission="data:manage"),
    _metadata("POST", "/data-sources", "Create a data source", permission="data:manage"),
    _metadata(
        "POST",
        "/data-sources/{id}/secret",
        "Set connection credentials",
        permission="data:manage",
        step_up=True,
    ),
    _metadata(
        "POST", "/data-sources/{id}/test", "Sanitized connectivity test", permission="data:manage"
    ),
    _metadata(
        "POST",
        "/data-sources/{id}/sync",
        "Run catalog sync (synchronous until worker-runtime)",
        permission="data:manage",
    ),
    _metadata("GET", "/data-sources/{id}", "Read a data source", permission="catalog:read"),
    _metadata(
        "GET", "/data-sources/{id}/tables", "Tables of a data source", permission="catalog:read"
    ),
    _metadata(
        "GET",
        "/data-sources/{id}/tables/{table_id}",
        "One table with columns and relationships",
        permission="catalog:read",
    ),
    _metadata(
        "GET", "/data-sources/{id}/sql-grants", "Per-connection SQL grants", role="org_admin"
    ),
    _metadata(
        "POST", "/data-sources/{id}/sql-grants", "Grant SQL on a connection", role="org_admin"
    ),
    _metadata(
        "DELETE",
        "/data-sources/{id}/sql-grants/{grant_id}",
        "Revoke a per-connection SQL grant",
        role="org_admin",
    ),
    # --- SQL (Section 13) ----------------------------------------------------------------------
    _query("POST", "/sql/validate", "Dry SQL validation", permission="sql:execute"),
    _query(
        "POST",
        "/sql/execute",
        "Execute validated SQL",
        permission="sql:execute",
        idempotency="no_store",
    ),
    _query(
        "GET",
        "/sql/history",
        "Query history",
        any_permission=frozenset({"sql:execute", "run:debug"}),
    ),
    # --- MCP (Sections 8.7, 14, 15) ---------------------------------------------------------------
    _mcp("GET", "/mcp/servers", "List MCP servers", permission="mcp:manage"),
    _mcp("POST", "/mcp/servers", "Register an MCP server", permission="mcp:manage"),
    _mcp("GET", "/mcp/servers/{id}", "An MCP server with its tools", permission="mcp:manage"),
    _mcp(
        "POST",
        "/mcp/servers/{id}/approve",
        "Approve an MCP server",
        role="org_admin",
        step_up=True,
    ),
    _mcp("POST", "/mcp/servers/{id}/disable", "Disable an approved server", role="org_admin"),
    _mcp("POST", "/mcp/servers/{id}/reject", "Reject a pending server", role="org_admin"),
    _mcp(
        "POST",
        "/mcp/servers/{id}/tools/{tool}/grants",
        "Grant a tool to a role or user",
        role="org_admin",
    ),
    _mcp(
        "DELETE",
        "/mcp/servers/{id}/tools/{tool}/grants/{grant_id}",
        "Revoke a tool grant",
        role="org_admin",
    ),
    _mcp(
        "POST",
        "/mcp/servers/{id}/tools/{tool}/invoke",
        "Invoke an MCP tool",
        idempotency="no_store",
    ),
    # --- Semantic (Section 12) --------------------------------------------------------------------
    _semantic("GET", "/semantic/metrics", "List metrics", permission="semantic:manage"),
    _semantic("POST", "/semantic/metrics", "Define a metric (draft)", permission="semantic:manage"),
    _semantic("GET", "/semantic/metrics/{id}", "Read a metric", permission="semantic:manage"),
    _semantic(
        "POST", "/semantic/metrics/{id}/approve", "Approve a metric", permission="semantic:manage"
    ),
    _semantic(
        "POST",
        "/semantic/metrics/{id}/deprecate",
        "Deprecate a metric",
        permission="semantic:manage",
    ),
    _semantic("GET", "/semantic/dimensions", "List dimensions", permission="semantic:manage"),
    _semantic("POST", "/semantic/dimensions", "Define a dimension", permission="semantic:manage"),
    # --- Billing (Section 23): owning service not assigned by Section 3 (ADR 0003) ---------------
    _stub(
        "GET",
        "/billing/usage",
        "Usage: tokens, query minutes, seats",
        None,
        "A11",
        permission="billing:read",
    ),
    # The token budget lives in analytics-orchestrator's ModelRouter ledger (Section 23; A10).
    _analytics(
        "GET", "/billing/quotas", "Today's LLM token budget and use", permission="billing:read"
    ),
    _stub(
        "POST",
        "/billing/subscription",
        "Change subscription",
        None,
        "A11",
        permission="billing:manage",
        step_up=True,
    ),
    # --- Notifications, webhooks, guest share -----------------------------------------------------
    _stub("GET", "/me/notifications", "The caller's notifications", "notification-service", "A11"),
    _stub(
        "POST",
        "/admin/webhooks",
        "Create a webhook (signing secret shown once)",
        "notification-service",
        "A11",
        role="org_admin",
        step_up=True,
        idempotency="no_store",
    ),
    _dashboard(
        "GET",
        "/share/{token}",
        "Read-only dashboard snapshot",
        public=True,
        rate_tier="public",
    ),
)
