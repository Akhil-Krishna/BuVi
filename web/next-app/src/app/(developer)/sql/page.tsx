import { listMfaFactors } from "@/features/auth/mfa-actions";
import { listDataSources, listTables } from "@/features/data-sources/actions";
import type { TableSummary } from "@/features/data-sources/types";
import { listSqlHistory } from "@/features/sql-lab/actions";
import { SqlLabWorkspace } from "@/features/sql-lab/SqlLabWorkspace";
import { getSession } from "@/lib/auth/session";

/** Stitch "SQL Lab" (Section 5.2), Phase B4 DoD: a developer browses the
 * catalog (B3's browser, reused here), writes/runs SQL, sees results, and
 * sends a result to the chart flow; a client-role session never reaches this
 * page (not in `CLIENT_ITEMS`'s nav) and still gets a real `403` from
 * query-gateway if it hits `/sql/*` directly, regardless of this page. */
export default async function SqlLabPage() {
  const session = await getSession();
  if (!session?.permissions.includes("sql:execute")) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-text-primary">SQL Lab</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your role does not include access to SQL Lab.
        </p>
      </div>
    );
  }

  const [dataSources, history, factors] = await Promise.all([
    listDataSources(),
    listSqlHistory(),
    listMfaFactors(),
  ]);
  const activeSources = dataSources.filter((source) => source.status === "active");
  const tableLists = await Promise.all(activeSources.map((source) => listTables(source.id)));
  const tables: TableSummary[] = tableLists.flat();
  const hasWebauthn = factors.some((factor) => factor.method === "webauthn");

  return (
    <div className="flex flex-1 flex-col">
      <h1 className="text-xl font-semibold text-text-primary">SQL Lab</h1>
      <div className="mt-4 flex flex-1 flex-col">
        <SqlLabWorkspace
          dataSources={activeSources}
          tables={tables}
          history={history}
          hasWebauthn={hasWebauthn}
          hasAnyFactor={factors.length > 0}
        />
      </div>
    </div>
  );
}
