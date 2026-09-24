import { describe, expect, it } from "vitest";
import type { ResultColumn } from "@/features/analytics/types";
import { buildChartPlan } from "./chart-spec";
import type { ChartSpec } from "./types";

const columns: ResultColumn[] = [
  { name: "month", type: "string" },
  { name: "revenue", type: "number" },
  { name: "segment", type: "string" },
];

const rows: unknown[][] = [
  ["Mar", 100, "Enterprise"],
  ["Jan", 10, "Enterprise"],
  ["Feb", 50, "Enterprise"],
  ["Jan", 5, "SMB"],
  ["Feb", 20, "SMB"],
  ["Mar", 40, "SMB"],
];

describe("buildChartPlan", () => {
  it("renders a table spec as a table, not a chart", () => {
    const spec: ChartSpec = { type: "table" };
    expect(buildChartPlan(spec, columns, rows)).toEqual({ kind: "table" });
  });

  it("rejects an encoding field that is not in the result schema", () => {
    const spec: ChartSpec = {
      type: "bar",
      encoding: {
        x: { field: "nope", type: "nominal" },
        y: { field: "revenue", type: "quantitative" },
      },
    };
    const plan = buildChartPlan(spec, columns, rows);
    expect(plan.kind).toBe("unsupported");
  });

  it("keeps category order as first-seen, not alphabetical, for a temporal-like field", () => {
    const spec: ChartSpec = {
      type: "line",
      encoding: {
        x: { field: "month", type: "temporal" },
        y: { field: "revenue", type: "quantitative" },
      },
    };
    const plan = buildChartPlan(spec, columns, rows);
    if (plan.kind !== "echarts") throw new Error("expected an echarts plan");
    const xAxis = plan.option.xAxis as { data: string[] };
    expect(xAxis.data).toEqual(["Mar", "Jan", "Feb"]);
  });

  it("splits into one series per distinct color-encoded value", () => {
    const spec: ChartSpec = {
      type: "bar",
      encoding: {
        x: { field: "month", type: "temporal" },
        y: { field: "revenue", type: "quantitative" },
        color: { field: "segment", type: "nominal" },
      },
    };
    const plan = buildChartPlan(spec, columns, rows);
    if (plan.kind !== "echarts") throw new Error("expected an echarts plan");
    const series = plan.option.series as Array<{ name: string; data: unknown[] }>;
    expect(series.map((s) => s.name).sort()).toEqual(["Enterprise", "SMB"]);
    expect(series.every((s) => s.data.length === 3)).toBe(true);
  });

  it("aligns a group's missing category to null rather than shifting the series", () => {
    const sparse = [...rows, ["Apr", 99, "Enterprise"]];
    const spec: ChartSpec = {
      type: "bar",
      encoding: {
        x: { field: "month", type: "temporal" },
        y: { field: "revenue", type: "quantitative" },
        color: { field: "segment", type: "nominal" },
      },
    };
    const plan = buildChartPlan(spec, columns, sparse);
    if (plan.kind !== "echarts") throw new Error("expected an echarts plan");
    const xAxis = plan.option.xAxis as { data: string[] };
    const series = plan.option.series as Array<{ name: string; data: unknown[] }>;
    const smb = series.find((s) => s.name === "SMB")!;
    expect(xAxis.data).toEqual(["Mar", "Jan", "Feb", "Apr"]);
    expect(smb.data[xAxis.data.indexOf("Apr")]).toBeNull();
  });

  it("builds pie slices from name/value pairs", () => {
    const spec: ChartSpec = {
      type: "pie",
      encoding: {
        x: { field: "month", type: "nominal" },
        y: { field: "revenue", type: "quantitative" },
      },
    };
    const plan = buildChartPlan(spec, columns, rows);
    if (plan.kind !== "echarts") throw new Error("expected an echarts plan");
    const series = plan.option.series as Array<{ data: Array<{ name: string; value: unknown }> }>;
    expect(series[0].data).toHaveLength(rows.length);
    expect(series[0].data[0]).toEqual({ name: "Mar", value: 100 });
  });
});
