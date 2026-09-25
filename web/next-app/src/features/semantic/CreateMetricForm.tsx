"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { getTable } from "@/features/data-sources/actions";
import type { Column, DataSource, TableSummary } from "@/features/data-sources/types";
import { createMetric } from "./actions";
import { composeExpression } from "./expression";
import { SynonymTagInput } from "./SynonymTagInput";
import type { Aggregation, MetricGrain } from "./types";

const AGGREGATIONS: Aggregation[] = ["sum", "avg", "min", "max", "count"];
const GRAINS: MetricGrain[] = ["day", "week", "month", "quarter", "year"];
const NUMERIC_TYPES = ["int", "numeric", "decimal", "real", "double", "float", "money", "serial"];

function tableKey(table: TableSummary): string {
  return `${table.data_source_id}::${table.id}`;
}

/**
 * Stitch "Business Metrics Catalog": a structured expression builder, not a
 * free-SQL field -- "For enterprise data integrity and deterministic SQL
 * translation, raw SQL entry is disabled" in the mock is real here, not just
 * copy: Section 8.3's v1 grammar is `AGG([DISTINCT] column)` over one table,
 * enforced both by this form's own choices (aggregation + column dropdowns,
 * never a text box) and, regardless, by semantic-service at write time. The
 * mock's "Filter Clause" field is omitted on purpose -- metric filters are
 * post-GA (Section 8.3), and a control for them here would be UI for a
 * feature the backend does not have.
 */
export function CreateMetricForm({
  dataSources,
  tables,
  onClose,
}: {
  dataSources: DataSource[];
  tables: TableSummary[];
  onClose: () => void;
}) {
  const router = useRouter();
  const [selectedTableKey, setSelectedTableKey] = useState(
    tables.length > 0 ? tableKey(tables[0]) : ""
  );
  const [columns, setColumns] = useState<Column[] | null>(null);
  const [aggregation, setAggregation] = useState<Aggregation>("sum");
  const [distinct, setDistinct] = useState(false);
  const [column, setColumn] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [defaultGrain, setDefaultGrain] = useState<MetricGrain | "">("month");
  const [synonyms, setSynonyms] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  // Reset synchronously during render when the table changes, rather than in
  // an effect (react.dev "You Might Not Need an Effect") -- the effect below
  // only owns the async fetch itself.
  const [columnsLoadedFor, setColumnsLoadedFor] = useState(selectedTableKey);
  if (selectedTableKey !== columnsLoadedFor) {
    setColumnsLoadedFor(selectedTableKey);
    setColumns(null);
    setColumn("");
  }

  const selectedTable = tables.find((table) => tableKey(table) === selectedTableKey) ?? null;
  const dataSourceById = useMemo(
    () => new Map(dataSources.map((source) => [source.id, source])),
    [dataSources]
  );

  useEffect(() => {
    if (!selectedTable) return;
    let cancelled = false;
    getTable(selectedTable.data_source_id, selectedTable.id).then((result) => {
      if (cancelled) return;
      if (result.ok) setColumns(result.data.columns);
    });
    return () => {
      cancelled = true;
    };
  }, [selectedTable]);

  const eligibleColumns = (columns ?? []).filter((candidate) => {
    if (candidate.is_pii) return false;
    if (aggregation === "sum" || aggregation === "avg") {
      return NUMERIC_TYPES.some((token) => candidate.data_type.toLowerCase().includes(token));
    }
    return true;
  });

  const expression = column ? composeExpression(aggregation, distinct, column) : "";

  function submit() {
    if (!selectedTable || !column || !name.trim()) {
      setError("Name, source table, and column are all required.");
      return;
    }
    setError(null);
    startTransition(async () => {
      const result = await createMetric({
        name: name.trim(),
        description: description.trim() || null,
        expression,
        baseTableId: selectedTable.id,
        defaultGrain: defaultGrain || null,
        synonyms,
      });
      if (!result.ok) {
        setError(result.message);
        return;
      }
      router.refresh();
      onClose();
    });
  }

  return (
    <div className="border border-border bg-bg p-4">
      <p className="text-sm font-medium text-text-primary">Define business metric</p>
      <p className="mt-1 text-xs text-text-secondary">
        Saved metrics start in Draft status. Approving is a separate step below.
      </p>

      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="text-xs font-semibold text-text-secondary">Metric display name</span>
          <input
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Gross Revenue"
            className="mt-1 w-full rounded-sm border border-border px-3 py-2 text-sm text-text-primary"
          />
        </label>
        <label className="block text-sm">
          <span className="text-xs font-semibold text-text-secondary">Default grain</span>
          <select
            value={defaultGrain}
            onChange={(event) => setDefaultGrain(event.target.value as MetricGrain | "")}
            className="mt-1 w-full rounded-sm border border-border px-3 py-2 text-sm text-text-primary"
          >
            <option value="">None</option>
            {GRAINS.map((grain) => (
              <option key={grain} value={grain}>
                {grain}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm sm:col-span-2">
          <span className="text-xs font-semibold text-text-secondary">Business definition</span>
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={2}
            placeholder="Total recognized sales after deductions and coupon discounts."
            className="mt-1 w-full resize-none rounded-sm border border-border px-3 py-2 text-sm text-text-primary"
          />
        </label>
      </div>

      <div className="mt-4 border-t border-border pt-4">
        <p className="text-xs font-semibold tracking-wide text-text-secondary uppercase">
          Expression generator
        </p>
        <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="block text-sm">
            <span className="text-xs font-semibold text-text-secondary">Source table</span>
            <select
              value={selectedTableKey}
              onChange={(event) => setSelectedTableKey(event.target.value)}
              className="mt-1 w-full rounded-sm border border-border px-2 py-1.5 text-sm text-text-primary"
            >
              {tables.length === 0 && <option value="">No tables synced</option>}
              {tables.map((table) => (
                <option key={tableKey(table)} value={tableKey(table)}>
                  {dataSourceById.get(table.data_source_id)?.name}: {table.schema_name}.
                  {table.table_name}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <span className="text-xs font-semibold text-text-secondary">Aggregation</span>
            <select
              value={aggregation}
              onChange={(event) => setAggregation(event.target.value as Aggregation)}
              className="mt-1 w-full rounded-sm border border-border px-2 py-1.5 text-sm text-text-primary"
            >
              {AGGREGATIONS.map((agg) => (
                <option key={agg} value={agg}>
                  {agg.toUpperCase()}
                </option>
              ))}
            </select>
            {aggregation === "count" && (
              <label className="mt-1 flex items-center gap-1.5 text-xs text-text-secondary">
                <input
                  type="checkbox"
                  checked={distinct}
                  onChange={(event) => setDistinct(event.target.checked)}
                />
                DISTINCT
              </label>
            )}
          </label>
          <label className="block text-sm">
            <span className="text-xs font-semibold text-text-secondary">Column</span>
            <select
              value={column}
              onChange={(event) => setColumn(event.target.value)}
              disabled={!columns}
              className="mt-1 w-full rounded-sm border border-border px-2 py-1.5 text-sm text-text-primary disabled:opacity-50"
            >
              <option value="">{columns ? "Choose a column" : "Loading..."}</option>
              {eligibleColumns.map((candidate) => (
                <option key={candidate.id} value={candidate.column_name}>
                  {candidate.column_name} ({candidate.data_type})
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="mt-3">
          <span className="text-xs font-semibold text-text-secondary">Live expression preview</span>
          <p
            data-testid="expression-preview"
            className="mt-1 rounded-sm border border-border bg-bg-subtle px-3 py-2 font-mono text-sm text-text-primary"
          >
            {expression || "—"}
          </p>
        </div>
      </div>

      <div className="mt-4">
        <span className="text-xs font-semibold text-text-secondary">
          Synonyms / natural-language aliases
        </span>
        <div className="mt-1">
          <SynonymTagInput synonyms={synonyms} onChange={setSynonyms} />
        </div>
      </div>

      <div className="mt-4 flex gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={isPending || !expression || !name.trim()}
          className="rounded-sm bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
        >
          Save as draft
        </button>
        <button
          type="button"
          onClick={onClose}
          className="rounded-sm border border-border px-4 py-1.5 text-sm text-text-primary hover:bg-bg-subtle"
        >
          Cancel
        </button>
      </div>
      {error && <p className="mt-3 text-sm text-danger">{error}</p>}
    </div>
  );
}
