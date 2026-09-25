import { listDataSources, listTables } from "@/features/data-sources/actions";
import type { TableSummary } from "@/features/data-sources/types";
import { listDimensions, listMetrics } from "@/features/semantic/actions";
import { SemanticWorkspace } from "@/features/semantic/SemanticWorkspace";
import { getSession } from "@/lib/auth/session";

/** Stitch "Business Metrics Catalog" (Section 5.2), Phase B5 DoD: a
 * developer defines/edits a metric, moves it draft -> approved ->
 * deprecated, and the next chat run uses an approved metric (Section 12 --
 * `_resolve_semantics` only ever sees `approved` metrics). Zero backend
 * changes: semantic-service has been built and tested since Phase A7. */
export default async function SemanticPage() {
  const session = await getSession();
  if (!session?.permissions.includes("semantic:manage")) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-text-primary">Business Metrics Catalog</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your role does not include access to semantic management.
        </p>
      </div>
    );
  }

  const [metrics, dimensions, dataSources] = await Promise.all([
    listMetrics(),
    listDimensions(),
    listDataSources(),
  ]);
  const activeSources = dataSources.filter((source) => source.status === "active");
  const tableLists = await Promise.all(activeSources.map((source) => listTables(source.id)));
  const tables: TableSummary[] = tableLists.flat().filter((table) => table.is_visible_to_agent);

  return (
    <SemanticWorkspace
      metrics={metrics}
      dimensions={dimensions}
      dataSources={activeSources}
      tables={tables}
    />
  );
}
