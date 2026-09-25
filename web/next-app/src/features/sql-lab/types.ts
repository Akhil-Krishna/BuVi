/** Mirrors `query_gateway.api.v1.sql` (Section 9, Phase A10). Snake_case on
 * the wire, like every REST response in this app except `AnalyticsRunEvent`. */
export type SqlColumn = { name: string; type: string };

export type SqlValidateResult = {
  query_id: string;
  valid: true;
  sql: string;
  tables: string[];
};

export type SqlExecuteResult = {
  query_id: string;
  columns: SqlColumn[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  truncation_reason: "rows" | "bytes" | null;
  duration_ms: number;
  tables: string[];
};

export type SqlHistoryItem = {
  id: string;
  data_source_id: string;
  requested_by: string;
  status: string;
  sql_text: string;
  row_count: number | null;
  duration_ms: number | null;
  error_code: string | null;
  created_at: string;
};
