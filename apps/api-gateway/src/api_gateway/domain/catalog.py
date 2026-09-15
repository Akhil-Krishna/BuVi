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

IDENTITY: Final = "identity-service"
METADATA: Final = "metadata-service"


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


def _stub(
    method: str, path: str, summary: str, backend: str | None, phase: str, **kwargs: object
) -> RouteSpec:
    return RouteSpec(method, path, summary, backend, available_in_phase=phase, **kwargs)  # type: ignore[arg-type]


CATALOG: Final[tuple[RouteSpec, ...]] = (
    # --- Auth (Sections 6.1, 6.6) --------------------------------------------------
    _identity("GET", "/auth/login", "Redirect to the IdP", public=True, rate_tier="auth"),
    _identity("GET", "/auth/callback", "OIDC callback", public=True, rate_tier="auth"),
    _identity("POST", "/auth/logout", "Revoke the session and IdP token"),
    _identity("GET", "/auth/session", "Current principal, roles, tenant"),
    _identity("POST", "/auth/mfa/enroll", "Start TOTP/WebAuthn enrollment"),
    _identity("POST", "/auth/mfa/verify", "Complete MFA", rate_tier="auth"),
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
    _identity("POST", "/me/api-keys", "Create an API key (secret shown once)"),
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
    _stub(
        "POST",
        "/conversations",
        "Create a conversation",
        "analytics-orchestrator",
        "A5",
        permission="chat:use",
    ),
    _stub(
        "POST",
        "/conversations/{id}/messages",
        "Send a message; returns run_id",
        "analytics-orchestrator",
        "A5",
        permission="chat:use",
    ),
    _stub(
        "GET",
        "/runs/{id}/events",
        "Run progress (SSE)",
        "analytics-orchestrator",
        "A5",
        permission="chat:use",
    ),
    _stub(
        "POST",
        "/runs/{id}/cancel",
        "Best-effort cancel",
        "analytics-orchestrator",
        "A5",
        permission="chat:use",
    ),
    # --- Artifacts, dashboards (Sections 16, 17) -----------------------------------------
    _stub(
        "GET",
        "/artifacts/{id}",
        "Read an artifact",
        "dashboard-service",
        "A6",
        permission="artifact:read",
    ),
    _stub(
        "GET",
        "/dashboards",
        "List dashboards",
        "dashboard-service",
        "A6",
        permission="dashboard:read",
    ),
    _stub(
        "POST",
        "/dashboards",
        "Create a dashboard",
        "dashboard-service",
        "A6",
        permission="dashboard:pin",
    ),
    _stub(
        "POST",
        "/dashboards/{id}/tiles",
        "Pin an artifact as a tile",
        "dashboard-service",
        "A6",
        permission="dashboard:pin",
    ),
    _stub(
        "PATCH",
        "/tiles/{id}",
        "Update tile layout/overrides",
        "dashboard-service",
        "A6",
        permission="dashboard:pin",
    ),
    _stub(
        "POST",
        "/dashboards/{id}/share-links",
        "Create a time-boxed share link",
        "dashboard-service",
        "A6",
        permission="dashboard:share",
        step_up=True,
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
    # metadata-service catalog reads beyond Section 9: the Phase A3 DoD's catalog API.
    _metadata(
        "GET",
        "/data-sources/{id}",
        "Read a data source",
        permission="data:manage",
        in_section_9=False,
        decision="ADR 0004 item 6",
    ),
    _metadata(
        "GET",
        "/data-sources/{id}/tables",
        "Catalog: tables of a data source",
        permission="data:manage",
        in_section_9=False,
        decision="ADR 0004 item 6",
    ),
    _metadata(
        "GET",
        "/data-sources/{id}/tables/{table_id}",
        "Catalog: one table with columns and relationships",
        permission="data:manage",
        in_section_9=False,
        decision="ADR 0004 item 6",
    ),
    # --- SQL (Section 13) ----------------------------------------------------------------------
    _stub(
        "POST",
        "/sql/validate",
        "Dry SQL validation",
        "query-gateway",
        "A4",
        permission="sql:execute",
    ),
    _stub(
        "POST",
        "/sql/execute",
        "Execute validated SQL",
        "query-gateway",
        "A4",
        permission="sql:execute",
    ),
    _stub(
        "GET",
        "/sql/history",
        "Query history",
        "query-gateway",
        "A4",
        any_permission=frozenset({"sql:execute", "run:debug"}),
    ),
    # --- MCP (Section 14) -----------------------------------------------------------------------
    _stub("GET", "/mcp/servers", "List MCP servers", "mcp-gateway", "A9", permission="mcp:manage"),
    _stub(
        "POST",
        "/mcp/servers",
        "Register an MCP server",
        "mcp-gateway",
        "A9",
        permission="mcp:manage",
    ),
    _stub(
        "POST",
        "/mcp/servers/{id}/approve",
        "Approve an MCP server",
        "mcp-gateway",
        "A10",
        role="org_admin",
        step_up=True,
    ),
    _stub(
        "POST", "/mcp/servers/{id}/tools/{tool}/invoke", "Invoke an MCP tool", "mcp-gateway", "A9"
    ),
    # --- Semantic (Section 12) --------------------------------------------------------------------
    _stub(
        "GET",
        "/semantic/metrics",
        "List metrics",
        "semantic-service",
        "A7",
        permission="semantic:manage",
    ),
    _stub(
        "POST",
        "/semantic/metrics",
        "Define a metric",
        "semantic-service",
        "A7",
        permission="semantic:manage",
    ),
    # --- Billing (Section 23): owning service not assigned by Section 3 (ADR 0003) ---------------
    _stub(
        "GET",
        "/billing/usage",
        "Usage: tokens, query minutes, seats",
        None,
        "A11",
        permission="billing:read",
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
    ),
    _stub(
        "GET",
        "/share/{token}",
        "Read-only dashboard snapshot",
        "dashboard-service",
        "A6",
        public=True,
        rate_tier="public",
    ),
)
