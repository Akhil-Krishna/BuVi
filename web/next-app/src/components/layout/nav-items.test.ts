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
    // Section 7.1: auditor holds catalog:read/audit:read, not chat:use --
    // they are not a "client" and must not be shown the chat/dashboard-pin nav.
    expect(navVariantFor(auditorSession)).toBe("developer-admin");
  });
});

describe("navItemsFor", () => {
  it("only shows items the session's own permissions actually grant", () => {
    const items = navItemsFor(developerSession).map((item) => item.label);
    expect(items).toEqual(["Data Sources", "SQL Lab", "Semantic"]);
  });

  it("never shows admin-only items to a plain developer", () => {
    const items = navItemsFor(developerSession).map((item) => item.label);
    expect(items).not.toContain("Users");
    expect(items).not.toContain("Policies");
  });

  it("shows only the read-only surfaces an auditor actually has", () => {
    const items = navItemsFor(auditorSession).map((item) => item.label);
    expect(items).toEqual(["Data Sources", "Audit"]);
  });
});
