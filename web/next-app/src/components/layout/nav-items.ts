import type { Session } from "@/lib/auth/session";

export type NavItem = { label: string; href: string; permission: string | null };

// Section 5.2: client and developer/admin each have their own Stitch nav
// screen. Which items actually render is driven by the session's real
// permissions (Section 7.1), not a single role-name switch -- a user can hold
// more than one role, and nav visibility is UX only (Section 7.4); the
// server-side check on each route is the real control either way.
export const CLIENT_ITEMS: NavItem[] = [
  { label: "Chat", href: "/chat", permission: "chat:use" },
  { label: "Dashboards", href: "/dashboards", permission: "dashboard:read" },
];

/** The developer/admin-only surfaces -- everything a `client` never sees. Kept separate from
 * `DEVELOPER_ADMIN_ITEMS` because this list, not the combined one, is what decides which nav
 * *variant* a session gets: if the check below looked at the combined list, a pure `client`
 * (who holds `chat:use`) would match and be handed the wide developer chrome. */
const DEVELOPER_ADMIN_ONLY: NavItem[] = [
  { label: "Data Sources", href: "/data", permission: "catalog:read" },
  { label: "SQL Lab", href: "/sql", permission: "sql:execute" },
  { label: "Semantic", href: "/semantic", permission: "semantic:manage" },
  { label: "MCP", href: "/mcp", permission: "mcp:manage" },
  { label: "Users", href: "/users", permission: "user:manage" },
  { label: "Policies", href: "/policies", permission: "policy:manage" },
  { label: "Audit", href: "/audit", permission: "audit:read" },
  { label: "Billing", href: "/billing", permission: "billing:read" },
  // Webhooks are an `org_admin` role check server-side (`require_org_admin`),
  // not a named Section 7.1 permission -- `user:manage` is reused as the nav
  // gate since only `org_admin` holds it.
  { label: "Webhooks", href: "/webhooks", permission: "user:manage" },
];

/** The developer/admin nav is a *superset* of the client one (ADR 0023).
 *
 * Section 5.2 has two separate Stitch nav screens, and B1 read that as two mutually exclusive
 * navs -- so a `developer`, who genuinely holds `chat:use`, `dashboard:read`, `dashboard:pin` and
 * `dashboard:share`, was shown no way to reach Chat or Dashboards even though every route and
 * server-side check allowed it. Building a chart in chat and pinning it is a core developer
 * workflow, so the wide nav now starts with the client items; `navItemsFor`'s permission filter
 * still decides what each session actually sees, which keeps an `auditor` (holds `dashboard:read`,
 * not `chat:use`) to Dashboards without Chat. */
export const DEVELOPER_ADMIN_ITEMS: NavItem[] = [...CLIENT_ITEMS, ...DEVELOPER_ADMIN_ONLY];

/** "Client Role View" vs "Developer & Admin View" -- the two Stitch nav
 * screens (Section 5.2). A user with only `client`-tier permissions gets the
 * client chrome; anyone with any developer/admin-tier permission gets the
 * other, wider one. */
export function navVariantFor(session: Pick<Session, "permissions">): "client" | "developer-admin" {
  const hasDeveloperAdminAccess = DEVELOPER_ADMIN_ONLY.some(
    (item) => item.permission && session.permissions.includes(item.permission)
  );
  return hasDeveloperAdminAccess ? "developer-admin" : "client";
}

export function navItemsFor(session: Pick<Session, "permissions">): NavItem[] {
  const items = navVariantFor(session) === "client" ? CLIENT_ITEMS : DEVELOPER_ADMIN_ITEMS;
  return items.filter((item) => !item.permission || session.permissions.includes(item.permission));
}
