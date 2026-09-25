"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { getTable } from "@/features/data-sources/actions";
import type { Column, DataSource, TableSummary } from "@/features/data-sources/types";
import { createDimension } from "./actions";
import { SynonymTagInput } from "./SynonymTagInput";

function tableKey(table: TableSummary): string {
  return `${table.data_source_id}::${table.id}`;
}

export function CreateDimensionForm({
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
  const [columnId, setColumnId] = useState("");
  const [name, setName] = useState("");
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
    setColumnId("");
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

  // Section 8.3: a dimension's column must not be PII, same as a metric's.
  const eligibleColumns = (columns ?? []).filter((candidate) => !candidate.is_pii);

  function submit() {
    if (!columnId || !name.trim()) {
      setError("Name and column are both required.");
      return;
    }
    setError(null);
    startTransition(async () => {
      const result = await createDimension({ name: name.trim(), columnId, synonyms });
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
      <p className="text-sm font-medium text-text-primary">Define dimension</p>

      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="text-xs font-semibold text-text-secondary">Name</span>
          <input
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Sales Region"
            className="mt-1 w-full rounded-sm border border-border px-3 py-2 text-sm text-text-primary"
          />
        </label>
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
        <label className="block text-sm sm:col-span-2">
          <span className="text-xs font-semibold text-text-secondary">Column</span>
          <select
            value={columnId}
            onChange={(event) => setColumnId(event.target.value)}
            disabled={!columns}
            className="mt-1 w-full rounded-sm border border-border px-2 py-1.5 text-sm text-text-primary disabled:opacity-50"
          >
            <option value="">{columns ? "Choose a column" : "Loading..."}</option>
            {eligibleColumns.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.column_name} ({candidate.data_type})
              </option>
            ))}
          </select>
        </label>
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
          disabled={isPending || !columnId || !name.trim()}
          className="rounded-sm bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
        >
          Save dimension
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
