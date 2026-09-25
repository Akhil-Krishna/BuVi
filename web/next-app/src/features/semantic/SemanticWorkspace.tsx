"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { getTable } from "@/features/data-sources/actions";
import type { DataSource, TableSummary } from "@/features/data-sources/types";
import { approveMetric, deprecateMetric } from "./actions";
import { CreateDimensionForm } from "./CreateDimensionForm";
import { CreateMetricForm } from "./CreateMetricForm";
import type { Dimension, Metric, MetricStatus } from "./types";

const STATUS_CLASS: Record<MetricStatus, string> = {
  draft: "border-warning-border bg-warning-bg text-warning",
  approved: "border-success-border bg-success-bg text-success",
  deprecated: "border-border bg-bg-subtle text-text-secondary",
};

function matches(haystack: string[], needle: string): boolean {
  const lowered = needle.trim().toLowerCase();
  if (!lowered) return true;
  return haystack.some((value) => value.toLowerCase().includes(lowered));
}

/** Stitch "Business Metrics Catalog" (Section 5.2). No matching Stitch
 * screen exists for dimensions -- they follow this same screen's list/create
 * pattern as a second tab (Section 5.2's fallback rule) rather than
 * inventing a different visual language for a conceptually similar
 * "named semantic concept over a catalog column" record. */
export function SemanticWorkspace({
  metrics,
  dimensions,
  dataSources,
  tables,
}: {
  metrics: Metric[];
  dimensions: Dimension[];
  dataSources: DataSource[];
  tables: TableSummary[];
}) {
  const router = useRouter();
  const [tab, setTab] = useState<"metrics" | "dimensions">("metrics");
  const [showCreateMetric, setShowCreateMetric] = useState(false);
  const [showCreateDimension, setShowCreateDimension] = useState(false);
  const [search, setSearch] = useState("");
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();
  const [columnLabels, setColumnLabels] = useState<Map<string, string> | null>(null);

  const approvedCount = metrics.filter((m) => m.status === "approved").length;
  const draftCount = metrics.filter((m) => m.status === "draft").length;
  const deprecatedCount = metrics.filter((m) => m.status === "deprecated").length;

  const visibleMetrics = metrics.filter((m) =>
    matches([m.name, ...m.synonyms, m.expression], search)
  );
  const visibleDimensions = dimensions.filter((d) => matches([d.name, ...d.synonyms], search));

  // Dimensions only store `column_id` -- resolve it to "schema.table.column"
  // for display by reading each synced table's columns once, lazily, only
  // when this tab is actually opened.
  useEffect(() => {
    if (tab !== "dimensions" || columnLabels) return;
    let cancelled = false;
    Promise.all(
      tables.map((table) =>
        getTable(table.data_source_id, table.id).then((result) =>
          result.ok
            ? result.data.columns.map(
                (col) =>
                  [col.id, `${table.schema_name}.${table.table_name}.${col.column_name}`] as const
              )
            : []
        )
      )
    ).then((groups) => {
      if (!cancelled) setColumnLabels(new Map(groups.flat()));
    });
    return () => {
      cancelled = true;
    };
  }, [tab, tables, columnLabels]);

  function approve(id: string) {
    setError(null);
    setPendingId(id);
    startTransition(async () => {
      const result = await approveMetric(id);
      if (!result.ok) setError(result.message);
      else router.refresh();
    });
  }

  function deprecate(id: string) {
    setError(null);
    setPendingId(id);
    startTransition(async () => {
      const result = await deprecateMetric(id);
      if (!result.ok) setError(result.message);
      else router.refresh();
    });
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-text-primary">Business Metrics Catalog</h1>
          <p className="mt-1 text-sm text-text-secondary">
            Canonical metric and dimension repository powering SQL Lab and the analytics chat.
          </p>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-3 gap-3">
        <div className="border border-success-border bg-success-bg p-3">
          <p className="text-xs text-text-secondary">Approved metrics</p>
          <p className="text-lg font-semibold text-text-primary">{approvedCount}</p>
          <p className="text-xs text-text-secondary">Reachable by the chat agent</p>
        </div>
        <div className="border border-warning-border bg-warning-bg p-3">
          <p className="text-xs text-text-secondary">Draft metrics</p>
          <p className="text-lg font-semibold text-text-primary">{draftCount}</p>
          <p className="text-xs text-text-secondary">Awaiting approval</p>
        </div>
        <div className="border border-border bg-bg-subtle p-3">
          <p className="text-xs text-text-secondary">Deprecated metrics</p>
          <p className="text-lg font-semibold text-text-primary">{deprecatedCount}</p>
          <p className="text-xs text-text-secondary">No longer used by the agent</p>
        </div>
      </div>

      <div className="mt-4 flex items-center justify-between gap-3">
        <div className="flex gap-4 border-b border-border">
          <button
            type="button"
            onClick={() => setTab("metrics")}
            className={`border-b-2 px-1 pb-2 text-sm font-medium ${
              tab === "metrics"
                ? "border-primary text-text-primary"
                : "border-transparent text-text-secondary hover:text-text-primary"
            }`}
          >
            Metrics
          </button>
          <button
            type="button"
            onClick={() => setTab("dimensions")}
            className={`border-b-2 px-1 pb-2 text-sm font-medium ${
              tab === "dimensions"
                ? "border-primary text-text-primary"
                : "border-transparent text-text-secondary hover:text-text-primary"
            }`}
          >
            Dimensions
          </button>
        </div>
        <input
          type="text"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search metrics, dimensions, synonyms..."
          className="w-64 rounded-sm border border-border px-3 py-1.5 text-sm text-text-primary"
        />
      </div>

      {tab === "metrics" && (
        <div className="mt-4">
          {!showCreateMetric && (
            <button
              type="button"
              onClick={() => setShowCreateMetric(true)}
              className="mb-3 rounded-sm bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-hover"
            >
              Define metric
            </button>
          )}
          {showCreateMetric && (
            <div className="mb-4">
              <CreateMetricForm
                dataSources={dataSources}
                tables={tables}
                onClose={() => setShowCreateMetric(false)}
              />
            </div>
          )}

          {visibleMetrics.length === 0 ? (
            <p className="text-sm text-text-secondary">No metrics defined yet.</p>
          ) : (
            <div className="overflow-x-auto border border-border bg-bg">
              <table className="w-full border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-border bg-bg-subtle">
                    <th className="px-3 py-2 font-semibold text-text-secondary">
                      Metric name &amp; synonyms
                    </th>
                    <th className="px-3 py-2 font-semibold text-text-secondary">Expression</th>
                    <th className="px-3 py-2 font-semibold text-text-secondary">Status</th>
                    <th className="px-3 py-2 text-right font-semibold text-text-secondary">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {visibleMetrics.map((metric, index) => (
                    <tr
                      key={metric.id}
                      className={`border-b border-border last:border-b-0 hover:bg-row-hover ${
                        index % 2 === 1 ? "bg-bg-subtle" : "bg-bg"
                      }`}
                    >
                      <td className="px-3 py-2">
                        <p className="font-medium text-text-primary">{metric.name}</p>
                        {metric.synonyms.length > 0 && (
                          <p className="text-xs text-text-secondary">
                            {metric.synonyms.join(", ")}
                          </p>
                        )}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-text-primary">
                        {metric.expression}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`rounded-sm border px-2 py-0.5 text-xs font-medium ${STATUS_CLASS[metric.status]}`}
                        >
                          {metric.status}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right">
                        {metric.status === "draft" && (
                          <button
                            type="button"
                            onClick={() => approve(metric.id)}
                            disabled={isPending && pendingId === metric.id}
                            className="rounded-sm border border-border px-2 py-1 text-xs text-success hover:bg-success hover:text-white disabled:opacity-50"
                          >
                            Approve
                          </button>
                        )}
                        {metric.status === "approved" && (
                          <button
                            type="button"
                            onClick={() => deprecate(metric.id)}
                            disabled={isPending && pendingId === metric.id}
                            className="rounded-sm border border-border px-2 py-1 text-xs text-danger hover:bg-danger hover:text-white disabled:opacity-50"
                          >
                            Deprecate
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === "dimensions" && (
        <div className="mt-4">
          {!showCreateDimension && (
            <button
              type="button"
              onClick={() => setShowCreateDimension(true)}
              className="mb-3 rounded-sm bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-hover"
            >
              Define dimension
            </button>
          )}
          {showCreateDimension && (
            <div className="mb-4">
              <CreateDimensionForm
                dataSources={dataSources}
                tables={tables}
                onClose={() => setShowCreateDimension(false)}
              />
            </div>
          )}

          {visibleDimensions.length === 0 ? (
            <p className="text-sm text-text-secondary">No dimensions defined yet.</p>
          ) : (
            <div className="overflow-x-auto border border-border bg-bg">
              <table className="w-full border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-border bg-bg-subtle">
                    <th className="px-3 py-2 font-semibold text-text-secondary">
                      Dimension name &amp; synonyms
                    </th>
                    <th className="px-3 py-2 font-semibold text-text-secondary">Column</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleDimensions.map((dimension, index) => (
                    <tr
                      key={dimension.id}
                      className={`border-b border-border last:border-b-0 hover:bg-row-hover ${
                        index % 2 === 1 ? "bg-bg-subtle" : "bg-bg"
                      }`}
                    >
                      <td className="px-3 py-2">
                        <p className="font-medium text-text-primary">{dimension.name}</p>
                        {dimension.synonyms.length > 0 && (
                          <p className="text-xs text-text-secondary">
                            {dimension.synonyms.join(", ")}
                          </p>
                        )}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-text-primary">
                        {columnLabels?.get(dimension.column_id) ?? dimension.column_id}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {error && <p className="mt-3 text-sm text-danger">{error}</p>}
    </div>
  );
}
