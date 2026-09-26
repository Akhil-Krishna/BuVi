import { describe, expect, it } from "vitest";
import { navItemsFor, navVariantFor } from "./nav-items";

const clientSession = { permissions: ["chat:use", "dashboard:read", "dashboard:pin"] };
const developerSession = { permissions: ["sql:execute", "catalog:read", "semantic:manage"] };
const orgAdminSession = { permissions: ["user:manage", "audit:read", "billing:read"] };
const auditorSession = { permissions: ["dashboard:read", "catalog:read", "audit:read"] };

describe("navVariantFor", () => {
  it("gives a pure client-permission session the client variant", () => {
    expect(navVariantFor(clientSession)).toBe("client");
  });

  it("gives a developer session the developer/admin variant", () => {
    expect(navVariantFor(developerSession)).toBe("developer-admin");
  });

  it("gives an org_admin session the developer/admin variant", () => {
    expect(navVariantFor(orgAdminSession)).toBe("developer-admin");
  });

  it("gives a read-only auditor the developer/admin variant, not client", () => {
    // Section 7.1: auditor holds catalog:read/audit:read, not chat:use.
    expect(navVariantFor(auditorSession)).toBe("developer-admin");
  });

  it("still gives a pure client the client variant now that the wide nav includes chat", () => {
    // ADR 0023 put the client items inside DEVELOPER_ADMIN_ITEMS. The variant check reads the
    // developer-only subset precisely so this case does not regress into the wide chrome.
    expect(navVariantFor(clientSession)).toBe("client");
    expect(navItemsFor(clientSession).map((i) => i.label)).toEqual(["Chat", "Dashboards"]);
  });
});

describe("navItemsFor", () => {
  it("only shows items the session's own permissions actually grant", () => {
    const items = navItemsFor(developerSession).map((item) => item.label);
    expect(items).toEqual(["Data Sources", "SQL Lab", "Semantic"]);
  });

  it("gives a real developer Chat and Dashboards alongside the developer surfaces", () => {
    // ADR 0023. The fixture above is a partial permission set; a real `developer` also holds
    // chat:use / dashboard:read / dashboard:pin / dashboard:share (platform_auth.permissions).
    const realDeveloper = {
      permissions: [
        "chat:use",
        "dashboard:read",
        "dashboard:pin",
        "dashboard:share",
        "catalog:read",
        "sql:execute",
        "semantic:manage",
      ],
    };
    const items = navItemsFor(realDeveloper).map((item) => item.label);
    expect(items).toEqual(["Chat", "Dashboards", "Data Sources", "SQL Lab", "Semantic"]);
    expect(items).not.toContain("Users");
  });

  it("gives an auditor Dashboards but never Chat (no chat:use)", () => {
    const items = navItemsFor(auditorSession).map((item) => item.label);
    expect(items).toContain("Dashboards");
    expect(items).not.toContain("Chat");
  });

  it("never shows admin-only items to a plain developer", () => {
    const items = navItemsFor(developerSession).map((item) => item.label);
    expect(items).not.toContain("Users");
    expect(items).not.toContain("Policies");
  });

  it("shows only the read-only surfaces an auditor actually has", () => {
    const items = navItemsFor(auditorSession).map((item) => item.label);
    // Dashboards is included from ADR 0023 onward: the auditor holds `dashboard:read`, and
    // read-only dashboard access is exactly what Section 7.1 grants them.
    expect(items).toEqual(["Dashboards", "Data Sources", "Audit"]);
  });
});
