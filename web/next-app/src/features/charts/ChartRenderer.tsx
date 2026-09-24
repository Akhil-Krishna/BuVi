"use client";

import * as echarts from "echarts";
import { useEffect, useRef } from "react";
import type { ResultColumn } from "@/features/analytics/types";
import { buildChartPlan } from "./chart-spec";
import type { ChartSpec } from "./types";

/**
 * Renders a validated `ChartSpec` (Section 17) against its result data.
 * Direct `echarts` (spec Section 5's "via `echarts-for-react` or direct")
 * rather than the wrapper package: it has no published React 19 peer range
 * yet, and a two-line init/dispose effect is all a single chart needs here.
 */
export function ChartRenderer({
  spec,
  columns,
  rows,
  height = 320,
}: {
  spec: ChartSpec;
  columns: ResultColumn[];
  rows: unknown[][];
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const plan = buildChartPlan(spec, columns, rows);

  useEffect(() => {
    if (plan.kind !== "echarts" || !containerRef.current) return;
    const instance = echarts.init(containerRef.current);
    instance.setOption(plan.option);
    const resize = () => instance.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      instance.dispose();
    };
    // `plan.option` is a fresh object every render; stringify keeps the
    // effect from tearing the chart down and rebuilding it on every parent
    // re-render when the underlying data has not actually changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan.kind === "echarts" ? JSON.stringify(plan.option) : plan.kind]);

  if (plan.kind === "table") {
    return <DataTable columns={columns} rows={rows} />;
  }
  if (plan.kind === "unsupported") {
    return <p className="p-4 text-sm text-text-secondary">{plan.reason}</p>;
  }
  return <div ref={containerRef} style={{ height }} className="w-full" />;
}

function DataTable({ columns, rows }: { columns: ResultColumn[]; rows: unknown[][] }) {
  return (
    <div className="max-h-80 overflow-auto border border-border">
      <table className="w-full border-collapse text-left text-sm">
        <thead className="sticky top-0 bg-bg-subtle">
          <tr>
            {columns.map((column) => (
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
          {rows.map((row, rowIndex) => (
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
  );
}
