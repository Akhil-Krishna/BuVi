"use client";

import { useEffect, useState } from "react";
import { getTable, listTables } from "./actions";
import type { TableDetail, TableSummary } from "./types";

function TableRow({ dataSourceId, table }: { dataSourceId: string; table: TableSummary }) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<TableDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function toggle() {
    if (expanded) {
      setExpanded(false);
      return;
    }
    setExpanded(true);
    if (detail) return;
    setLoading(true);
    getTable(dataSourceId, table.id).then((result) => {
      setLoading(false);
      if (result.ok) setDetail(result.data);
      else setError(result.message);
    });
  }

  return (
    <div className="border-b border-border last:border-b-0">
      <button
        type="button"
        onClick={toggle}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-row-hover"
      >
        <span className="font-mono text-text-primary">
          {table.schema_name}.{table.table_name}
        </span>
        <span className="text-xs text-text-secondary">
          {table.column_count} columns
          {table.row_count_estimate !== null && ` · ~${table.row_count_estimate} rows`}
        </span>
      </button>
      {expanded && (
        <div className="border-t border-border bg-bg-subtle px-3 py-3">
          {loading && <p className="text-sm text-text-secondary">Loading...</p>}
          {error && <p className="text-sm text-danger">{error}</p>}
          {detail && (
            <>
              <table className="w-full border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-border">
                    <th className="py-1 pr-3 font-semibold text-text-secondary">Column</th>
                    <th className="py-1 pr-3 font-semibold text-text-secondary">Type</th>
                    <th className="py-1 font-semibold text-text-secondary">PII</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.columns.map((column) => (
                    <tr key={column.id} className="border-b border-border last:border-b-0">
                      <td className="py-1 pr-3 font-mono text-text-primary">
                        {column.column_name}
                      </td>
                      <td className="py-1 pr-3 text-text-secondary">{column.data_type}</td>
                      <td className="py-1 text-text-secondary">{column.is_pii ? "Yes" : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {detail.relationships.length > 0 && (
                <div className="mt-3">
                  <p className="text-xs font-semibold text-text-secondary">Relationships</p>
                  <ul className="mt-1 space-y-1 text-xs text-text-secondary">
                    {detail.relationships.map((rel) => (
                      <li key={rel.id} className="font-mono">
                        {rel.from_column.table_name}.{rel.from_column.column_name} →{" "}
                        {rel.to_column.table_name}.{rel.to_column.column_name}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function TableBrowser({ dataSourceId }: { dataSourceId: string }) {
  const [tables, setTables] = useState<TableSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listTables(dataSourceId)
      .then((items) => {
        if (!cancelled) setTables(items);
      })
      .catch(() => {
        if (!cancelled) setError("Tables could not be loaded.");
      });
    return () => {
      cancelled = true;
    };
  }, [dataSourceId]);

  if (error) return <p className="text-sm text-danger">{error}</p>;
  if (!tables) return <p className="text-sm text-text-secondary">Loading tables...</p>;
  if (tables.length === 0) {
    return <p className="text-sm text-text-secondary">No tables synced yet.</p>;
  }

  return (
    <div className="border border-border bg-bg">
      {tables.map((table) => (
        <TableRow key={table.id} dataSourceId={dataSourceId} table={table} />
      ))}
    </div>
  );
}
