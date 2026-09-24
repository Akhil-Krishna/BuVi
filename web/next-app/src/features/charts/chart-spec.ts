import type { ResultColumn } from "@/features/analytics/types";
import type { ChartOptions, ChartSpec } from "./types";

export type ChartRenderPlan =
  | { kind: "table" }
  | { kind: "echarts"; option: Record<string, unknown> }
  | { kind: "unsupported"; reason: string };

/** Enterprise, single-mode palette (Section 5.1/5.2) -- no gradients, derived
 * from the same token hues the rest of the UI uses rather than a generic
 * charting-library rainbow. */
const CATEGORICAL = ["#1e4fb8", "#0e7cd6", "#1e8e5a", "#b7791f", "#7a4fb8", "#5b6b82"];
const SEQUENTIAL = ["#dbe6f8", "#a9c3ee", "#6f97dd", "#3e6cc6", "#1e4fb8", "#123472"];
const DIVERGING = ["#c62828", "#e08585", "#f0d9d9", "#d9e3f0", "#7fa5df", "#1e4fb8"];

function paletteFor(scheme: ChartOptions["colorScheme"], count: number): string[] {
  const base =
    scheme === "sequential" ? SEQUENTIAL : scheme === "diverging" ? DIVERGING : CATEGORICAL;
  const out: string[] = [];
  for (let i = 0; i < Math.max(count, 1); i += 1) out.push(base[i % base.length]);
  return out;
}

function columnIndex(columns: ResultColumn[], field: string | null | undefined): number {
  if (!field) return -1;
  return columns.findIndex((column) => column.name === field);
}

function dedupeInOrder(values: string[]): string[] {
  const seen: string[] = [];
  for (const value of values) if (!seen.includes(value)) seen.push(value);
  return seen;
}

/**
 * `ChartSpec` (Section 17) + an artifact's tabular result -> an ECharts
 * `option`. Never trusts JS/HTML from the payload -- every value here is
 * either a bounded enum already checked by `isChartType`/`parseChartSpec` or
 * a value read out of the result's own typed columns.
 */
export function buildChartPlan(
  spec: ChartSpec,
  columns: ResultColumn[],
  rows: unknown[][]
): ChartRenderPlan {
  if (spec.type === "table") return { kind: "table" };

  const xField = spec.encoding?.x?.field;
  const yField = spec.encoding?.y?.field;
  const xIdx = columnIndex(columns, xField);
  const yIdx = columnIndex(columns, yField);
  if (xIdx === -1 || yIdx === -1) {
    return { kind: "unsupported", reason: "This chart references a field not in the result." };
  }
  const colorIdx = columnIndex(columns, spec.encoding?.color?.field);

  const options = spec.options ?? {};
  const legend = options.legend ?? true;
  const titleText = options.title ?? undefined;
  const title = titleText ? { text: titleText, left: 0, textStyle: { fontSize: 13 } } : undefined;

  if (spec.type === "pie") {
    return {
      kind: "echarts",
      option: {
        title,
        tooltip: { trigger: "item" },
        legend: legend ? { bottom: 0, type: "scroll" } : undefined,
        color: paletteFor(options.colorScheme, rows.length),
        series: [
          {
            type: "pie",
            radius: "65%",
            center: legend ? ["50%", "44%"] : ["50%", "50%"],
            data: rows.map((row) => ({ name: String(row[xIdx]), value: row[yIdx] })),
          },
        ],
      },
    };
  }

  if (spec.type === "scatter") {
    const groups =
      colorIdx === -1 ? [null] : dedupeInOrder(rows.map((row) => String(row[colorIdx])));
    const series = groups.map((group) => ({
      type: "scatter",
      name: group ?? yField,
      data: (group === null ? rows : rows.filter((row) => String(row[colorIdx]) === group)).map(
        (row) => [row[xIdx], row[yIdx]]
      ),
    }));
    return {
      kind: "echarts",
      option: {
        title,
        tooltip: { trigger: "item" },
        legend: legend && groups.length > 1 ? { bottom: 0 } : undefined,
        grid: { left: 48, right: 16, top: title ? 36 : 16, bottom: 32, containLabel: true },
        xAxis: { type: "value", name: options.xAxisLabel ?? undefined },
        yAxis: { type: "value", name: options.yAxisLabel ?? undefined },
        color: paletteFor(options.colorScheme, series.length),
        series,
      },
    };
  }

  // line / bar / area: a category axis, one series per distinct `color`
  // value if the spec has one. Categories keep first-seen order rather than
  // sorting, so a temporal field like "month" stays chronological.
  const categories = dedupeInOrder(rows.map((row) => String(row[xIdx])));
  const groups = colorIdx === -1 ? [null] : dedupeInOrder(rows.map((row) => String(row[colorIdx])));
  const stack = options.stacking && options.stacking !== "none" ? "total" : undefined;

  const series = groups.map((group) => {
    const groupRows = group === null ? rows : rows.filter((row) => String(row[colorIdx]) === group);
    const byCategory = new Map<string, unknown>();
    for (const row of groupRows) byCategory.set(String(row[xIdx]), row[yIdx]);
    return {
      type: spec.type === "area" ? "line" : spec.type,
      name: group ?? yField,
      stack,
      areaStyle: spec.type === "area" ? {} : undefined,
      data: categories.map((category) => byCategory.get(category) ?? null),
    };
  });

  return {
    kind: "echarts",
    option: {
      title,
      tooltip: { trigger: "axis" },
      legend: legend && groups.length > 1 ? { bottom: 0, type: "scroll" } : undefined,
      grid: {
        left: 48,
        right: 16,
        top: title ? 36 : 16,
        bottom: legend && groups.length > 1 ? 40 : 32,
        containLabel: true,
      },
      xAxis: { type: "category", data: categories, name: options.xAxisLabel ?? undefined },
      yAxis: { type: "value", name: options.yAxisLabel ?? undefined },
      color: paletteFor(options.colorScheme, series.length),
      series,
    },
  };
}
