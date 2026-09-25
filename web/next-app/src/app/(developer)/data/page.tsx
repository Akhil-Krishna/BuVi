import { listMfaFactors } from "@/features/auth/mfa-actions";
import { listDataSources, listTenantUsersForGrant } from "@/features/data-sources/actions";
import { DataSourcesWorkspace } from "@/features/data-sources/DataSourcesWorkspace";
import { getSession } from "@/lib/auth/session";

/** Stitch "Data Sources" (Section 5.2), Phase B3 DoD: a developer can add a
 * connection, see it `pending` until a secret is set, test it, sync its
 * catalog, and browse tables/columns; an org_admin can grant/revoke a
 * specific user's `sql:execute`; a client-role user never reaches this page
 * (not in `CLIENT_ITEMS`'s nav, and every mutating call below is re-checked
 * server-side regardless of what this page renders). */
export default async function DataSourcesPage() {
  const session = await getSession();
  if (!session?.permissions.includes("catalog:read")) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-text-primary">Data Sources</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your role does not include access to data sources.
        </p>
      </div>
    );
  }

  const canManage = session.permissions.includes("data:manage");
  const isOrgAdmin = session.roles.includes("org_admin");

  const [dataSources, factors, tenantUsers] = await Promise.all([
    listDataSources(),
    canManage ? listMfaFactors() : Promise.resolve([]),
    isOrgAdmin ? listTenantUsersForGrant() : Promise.resolve([]),
  ]);
  const hasWebauthn = factors.some((factor) => factor.method === "webauthn");

  return (
    <DataSourcesWorkspace
      dataSources={dataSources}
      canManage={canManage}
      canReadCatalog={session.permissions.includes("catalog:read")}
      isOrgAdmin={isOrgAdmin}
      hasWebauthn={hasWebauthn}
      hasAnyFactor={factors.length > 0}
      tenantUsers={tenantUsers}
    />
  );
}
