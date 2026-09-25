import { listMfaFactors } from "@/features/auth/mfa-actions";
import { listTenantUsersForGrant } from "@/features/data-sources/actions";
import { listServers } from "@/features/mcp/actions";
import { McpWorkspace } from "@/features/mcp/McpWorkspace";
import { getSession } from "@/lib/auth/session";

/** Phase B6 DoD: registering a server shows `pending_approval`; approving
 * (org_admin, step-up) makes its read tools invokable; a write/admin tool
 * needs step-up at invoke; a rejected/disabled server's tools stay refused
 * even from an already-open page. Zero backend changes -- mcp-gateway has
 * been built and tested since Phase A9/A10. */
export default async function McpPage() {
  const session = await getSession();
  if (!session?.permissions.includes("mcp:manage")) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-text-primary">MCP Server Governance</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your role does not include access to MCP governance.
        </p>
      </div>
    );
  }

  const isOrgAdmin = session.roles.includes("org_admin");
  const [servers, factors, users] = await Promise.all([
    listServers(),
    listMfaFactors(),
    isOrgAdmin ? listTenantUsersForGrant() : Promise.resolve([]),
  ]);
  const hasWebauthn = factors.some((f) => f.method === "webauthn");

  return (
    <McpWorkspace
      servers={servers}
      isOrgAdmin={isOrgAdmin}
      users={users}
      hasWebauthn={hasWebauthn}
      hasAnyFactor={factors.length > 0}
    />
  );
}
