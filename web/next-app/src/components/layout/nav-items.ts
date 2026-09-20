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

export const DEVELOPER_ADMIN_ITEMS: NavItem[] = [
  { label: "Data Sources", href: "/data", permission: "catalog:read" },
  { label: "SQL Lab", href: "/sql", permission: "sql:execute" },
  { label: "Semantic", href: "/semantic", permission: "semantic:manage" },
  { label: "MCP", href: "/mcp", permission: "mcp:manage" },
  { label: "Users", href: "/users", permission: "user:manage" },
  { label: "Policies", href: "/policies", permission: "policy:manage" },
  { label: "Audit", href: "/audit", permission: "audit:read" },
  { label: "Billing", href: "/billing", permission: "billing:read" },
];

/** "Client Role View" vs "Developer & Admin View" -- the two Stitch nav
 * screens (Section 5.2). A user with only `client`-tier permissions gets the
 * client chrome; anyone with any developer/admin-tier permission gets the
 * other, wider one. */
export function navVariantFor(session: Pick<Session, "permissions">): "client" | "developer-admin" {
  const hasDeveloperAdminAccess = DEVELOPER_ADMIN_ITEMS.some(
    (item) => item.permission && session.permissions.includes(item.permission)
  );
  return hasDeveloperAdminAccess ? "developer-admin" : "client";
}

export function navItemsFor(session: Pick<Session, "permissions">): NavItem[] {
  const items = navVariantFor(session) === "client" ? CLIENT_ITEMS : DEVELOPER_ADMIN_ITEMS;
  return items.filter((item) => !item.permission || session.permissions.includes(item.permission));
}
