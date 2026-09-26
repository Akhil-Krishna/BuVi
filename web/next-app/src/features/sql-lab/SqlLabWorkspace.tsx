"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { MfaVerifyForm } from "@/features/auth/MfaVerifyForm";
import type { DataSource, TableSummary } from "@/features/data-sources/types";
import { executeSql, validateSql } from "./actions";
import { SqlEditor } from "./SqlEditor";
import type { SqlExecuteResult, SqlHistoryItem, SqlValidateResult } from "./types";
import { formatCount, formatDateTime } from "@/lib/format";

const ROW_LIMITS = [100, 1_000, 10_000, 25_000, 50_000];

/**
 * Stitch "SQL Lab" (Section 5.2): a data-source-scoped editor, a toolbar, and
 * a results/history split -- built against exactly what `query-gateway`
 * exposes (validate/execute/history), not the mock's tabs-of-saved-queries
 * or CSV export, which nothing in Section 9 backs.
 */
export function SqlLabWorkspace({
  dataSources,
  tables,
  history,
  hasWebauthn,
  hasAnyFactor,
}: {
  dataSources: DataSource[];
  tables: TableSummary[];
  history: SqlHistoryItem[];
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
}) {
  const router = useRouter();
  const [dataSourceId, setDataSourceId] = useState(dataSources[0]?.id ?? "");
  const [sql, setSql] = useState("");
  const [rowLimit, setRowLimit] = useState(ROW_LIMITS[1]);
  const [tab, setTab] = useState<"results" | "history">("results");
  const [validation, setValidation] = useState<SqlValidateResult | null>(null);
  const [result, setResult] = useState<SqlExecuteResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingStepUp, setPendingStepUp] = useState(false);
  const [requireWebauthn, setRequireWebauthn] = useState(false);
  const [isPending, startTransition] = useTransition();

  const tablesForSource = useMemo(
    () => tables.filter((table) => table.data_source_id === dataSourceId),
    [tables, dataSourceId]
  );

  function insertTableName(table: TableSummary) {
    setSql(
      (current) =>
        `${current}${current && !current.endsWith(" ") ? " " : ""}${table.schema_name}.${table.table_name}`
    );
  }

  function runValidate() {
    if (!dataSourceId || !sql.trim()) return;
    setError(null);
    setValidation(null);
    startTransition(async () => {
      const outcome = await validateSql(dataSourceId, sql);
      if (outcome.ok) setValidation(outcome.data);
      else setError(outcome.message);
    });
  }

  function runExecute() {
    if (!dataSourceId || !sql.trim()) return;
    setError(null);
    setResult(null);
    setPendingStepUp(false);
    startTransition(async () => {
      const outcome = await executeSql(dataSourceId, sql, rowLimit);
      if (outcome.ok) {
        setResult(outcome.data);
        setTab("results");
        router.refresh(); // picks up the new row in `history`
        return;
      }
      if (outcome.code === "STEP_UP_REQUIRED") {
        if (!hasAnyFactor) {
          setError("Add a two-factor method in Account settings, then try again.");
          return;
        }
        setRequireWebauthn(outcome.details.method === "webauthn");
        setPendingStepUp(true);
        return;
      }
      setError(outcome.message);
    });
  }

  function loadFromHistory(item: SqlHistoryItem) {
    setSql(item.sql_text);
    if (dataSources.some((source) => source.id === item.data_source_id)) {
      setDataSourceId(item.data_source_id);
    }
    setTab("results");
    setResult(null);
    setValidation(null);
    setError(null);
  }

  function sendToChat() {
    const oneLine = sql.trim().replace(/\s+/g, " ");
    const prompt = `Chart these results: ${oneLine}`;
    const params = new URLSearchParams({ prompt, dataSourceId });
    router.push(`/chat?${params.toString()}`);
  }

  if (pendingStepUp) {
    return (
      <MfaVerifyForm
        hasWebauthn={hasWebauthn}
        requireWebauthn={requireWebauthn}
        onVerified={() => {
          setPendingStepUp(false);
          runExecute();
        }}
      />
    );
  }

  return (
    <div className="flex flex-1 gap-4">
      <aside className="w-64 shrink-0 border border-border bg-bg">
        <div className="border-b border-border p-3">
          <label
            className="block text-xs font-semibold text-text-secondary"
            htmlFor="sql-lab-source"
          >
            Data source
          </label>
          <select
            id="sql-lab-source"
            value={dataSourceId}
            onChange={(event) => setDataSourceId(event.target.value)}
            className="mt-1 w-full rounded-sm border border-border px-2 py-1.5 text-sm text-text-primary"
          >
            {dataSources.length === 0 && <option value="">No data sources</option>}
            {dataSources.map((source) => (
              <option key={source.id} value={source.id}>
                {source.name}
              </option>
            ))}
          </select>
        </div>
        <div className="p-3">
          <p className="text-xs font-semibold tracking-wide text-text-secondary uppercase">
            Tables
          </p>
          {tablesForSource.length === 0 ? (
            <p className="mt-2 text-xs text-text-secondary">
              No tables synced for this source yet.
            </p>
          ) : (
            <ul className="mt-2 space-y-0.5">
              {tablesForSource.map((table) => (
                <li key={table.id}>
                  <button
                    type="button"
                    onClick={() => insertTableName(table)}
                    className="w-full truncate rounded-sm px-1.5 py-1 text-left font-mono text-xs text-text-primary hover:bg-row-hover"
                    title={`Insert ${table.schema_name}.${table.table_name}`}
                  >
                    {table.schema_name}.{table.table_name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>

      <div className="flex flex-1 flex-col gap-3">
        <div className="border border-border bg-bg">
          <SqlEditor
            value={sql}
            onChange={setSql}
            onRun={runExecute}
            placeholder="select * from sales.orders limit 100;"
          />
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border bg-bg-subtle px-3 py-2">
            <div className="flex items-center gap-2">
              <label className="text-xs text-text-secondary" htmlFor="sql-lab-limit">
                Row limit
              </label>
              <select
                id="sql-lab-limit"
                value={rowLimit}
                onChange={(event) => setRowLimit(Number(event.target.value))}
                className="rounded-sm border border-border px-2 py-1 text-xs text-text-primary"
              >
                {ROW_LIMITS.map((limit) => (
                  <option key={limit} value={limit}>
                    {formatCount(limit)}
                  </option>
                ))}
              </select>
              <span className="text-xs text-text-secondary">⌘/Ctrl+Enter to run</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={runValidate}
                disabled={isPending || !dataSourceId || !sql.trim()}
                className="rounded-sm border border-border px-3 py-1.5 text-sm text-text-primary hover:bg-bg disabled:opacity-50"
              >
                Validate
              </button>
              <button
                type="button"
                onClick={runExecute}
                disabled={isPending || !dataSourceId || !sql.trim()}
                className="rounded-sm bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
              >
                Run
              </button>
            </div>
          </div>
        </div>

        {validation && (
          <p className="text-sm text-success">
            Valid. References: {validation.tables.join(", ") || "none"}
          </p>
        )}
        {error && <p className="text-sm text-danger">{error}</p>}

        <div>
          <div className="flex gap-4 border-b border-border">
            <button
              type="button"
              onClick={() => setTab("results")}
              className={`border-b-2 px-1 pb-2 text-sm font-medium ${
                tab === "results"
                  ? "border-primary text-text-primary"
                  : "border-transparent text-text-secondary hover:text-text-primary"
              }`}
            >
              Results
            </button>
            <button
              type="button"
              onClick={() => setTab("history")}
              className={`border-b-2 px-1 pb-2 text-sm font-medium ${
                tab === "history"
                  ? "border-primary text-text-primary"
                  : "border-transparent text-text-secondary hover:text-text-primary"
              }`}
            >
              Query History
            </button>
          </div>

          {tab === "results" && (
            <div className="mt-3">
              {!result ? (
                <p className="text-sm text-text-secondary">Run a query to see results here.</p>
              ) : (
                <>
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <p className="text-xs text-text-secondary">
                      {formatCount(result.row_count)} rows in {result.duration_ms}ms
                      {result.truncated && ` -- truncated (${result.truncation_reason})`}
                    </p>
                    <button
                      type="button"
                      onClick={sendToChat}
                      className="rounded-sm border border-border px-3 py-1.5 text-sm text-text-primary hover:bg-bg-subtle"
                    >
                      Send to Chat/Chart
                    </button>
                  </div>
                  <div className="max-h-96 overflow-auto border border-border bg-bg">
                    <table className="w-full border-collapse text-left text-sm">
                      <thead className="sticky top-0 bg-bg-subtle">
                        <tr>
                          {result.columns.map((column) => (
                            <th
                              key={column.name}
                              className="border-b border-border px-3 py-2 font-semibold text-text-secondary"
                            >
                              {column.name}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {result.rows.map((row, rowIndex) => (
                          <tr
                            key={rowIndex}
                            className="border-b border-border last:border-b-0 hover:bg-row-hover"
                          >
                            {row.map((value, cellIndex) => (
                              <td key={cellIndex} className="px-3 py-2 font-mono text-text-primary">
                                {value === null || value === undefined ? "—" : String(value)}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          )}

          {tab === "history" && (
            <div className="mt-3 overflow-x-auto border border-border bg-bg">
              {history.length === 0 ? (
                <p className="p-3 text-sm text-text-secondary">No queries run yet.</p>
              ) : (
                <table className="w-full border-collapse text-left text-sm">
                  <thead>
                    <tr className="border-b border-border bg-bg-subtle">
                      <th className="px-3 py-2 font-semibold text-text-secondary">SQL</th>
                      <th className="px-3 py-2 font-semibold text-text-secondary">Status</th>
                      <th className="px-3 py-2 font-semibold text-text-secondary">Rows</th>
                      <th className="px-3 py-2 font-semibold text-text-secondary">When</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((item) => (
                      <tr
                        key={item.id}
                        className="cursor-pointer border-b border-border last:border-b-0 hover:bg-row-hover"
                        onClick={() => loadFromHistory(item)}
                      >
                        <td className="max-w-xs truncate px-3 py-2 font-mono text-xs text-text-primary">
                          {item.sql_text}
                        </td>
                        <td className="px-3 py-2 text-text-secondary">{item.status}</td>
                        <td className="px-3 py-2 text-text-secondary">{item.row_count ?? "—"}</td>
                        <td className="px-3 py-2 text-text-secondary">
                          {formatDateTime(item.created_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
