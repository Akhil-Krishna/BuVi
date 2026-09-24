/** Mirrors `platform_contracts` `ChartSpec`/`Encoding`/`ChartOptions`
 * (contracts/json-schema/ChartSpec.json, Section 17). visualization-service
 * already validated this server-side against the strict schema; this app
 * still narrows/guards it at render time (`isChartType`) rather than trusting
 * the cast, since dashboard-service's own response model does not re-check it
 * (`additionalProperties: true` there) and a chart payload is a trust
 * boundary either way (Section 37). */
export type ChartType = "line" | "bar" | "area" | "scatter" | "pie" | "table";

const CHART_TYPES: readonly ChartType[] = ["line", "bar", "area", "scatter", "pie", "table"];

export function isChartType(value: unknown): value is ChartType {
  return typeof value === "string" && (CHART_TYPES as readonly string[]).includes(value);
}

export type EncodingType = "temporal" | "quantitative" | "nominal" | "ordinal";

export type Encoding = { field: string; type: EncodingType };

export type ChartOptions = {
  title?: string | null;
  legend?: boolean;
  stacking?: "none" | "stacked" | "percent" | null;
  colorScheme?: "default" | "categorical" | "sequential" | "diverging" | null;
  xAxisLabel?: string | null;
  yAxisLabel?: string | null;
};

export type ChartSpec = {
  type: ChartType;
  dataset?: "artifact-result";
  encoding?: { x?: Encoding | null; y?: Encoding | null; color?: Encoding | null };
  options?: ChartOptions;
};

export function parseChartSpec(raw: Record<string, unknown>): ChartSpec | null {
  if (!isChartType(raw.type)) return null;
  return raw as unknown as ChartSpec;
}
