/** Mirrors `semantic_service.api.v1.schemas` (Section 8.3, Phase A7). Snake_case
 * on the wire, like every REST response in this app except `AnalyticsRunEvent`. */
export type MetricStatus = "draft" | "approved" | "deprecated";
export type MetricGrain = "day" | "week" | "month" | "quarter" | "year";
export type Aggregation = "sum" | "avg" | "min" | "max" | "count";

export type Metric = {
  id: string;
  name: string;
  description: string | null;
  expression: string;
  default_grain: MetricGrain | null;
  base_table_id: string;
  synonyms: string[];
  status: MetricStatus;
  created_by: string;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
};

export type Dimension = {
  id: string;
  name: string;
  column_id: string;
  synonyms: string[];
  created_by: string;
  created_at: string;
};
