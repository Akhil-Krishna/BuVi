/** Mirrors `metadata_service.api.v1.schemas` (Section 8.2, 13.1). Snake_case
 * on the wire, like every REST response in this app except `AnalyticsRunEvent`. */
export type DataSourceEngine = "postgres" | "mysql" | "snowflake" | "bigquery" | "redshift";
export type DataSourceStatus = "pending" | "active" | "error";

export type DataSource = {
  id: string;
  tenant_id: string;
  name: string;
  engine: DataSourceEngine;
  host_label: string;
  database_name: string;
  allowed_schemas: string[];
  capabilities: Record<string, unknown>;
  status: DataSourceStatus;
  last_sync_at: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
};

export type ConnectivityTestResult = {
  data_source_id: string;
  ok: boolean;
  code: string;
  /** Already a safe, complete sentence -- never raw driver text (Section 13.1). */
  message: string;
  tables_discovered: number | null;
  latency_ms: number;
  status: DataSourceStatus;
};

export type SyncResult = {
  data_source_id: string;
  ok: boolean;
  code: string;
  message: string;
  status: DataSourceStatus;
  tables_synced: number;
  columns_synced: number;
  relationships_synced: number;
  snapshot_id: string | null;
  synced_at: string;
};

export type TableSummary = {
  id: string;
  data_source_id: string;
  schema_name: string;
  table_name: string;
  description: string | null;
  row_count_estimate: number | null;
  is_visible_to_agent: boolean;
  column_count: number;
};

export type Column = {
  id: string;
  column_name: string;
  data_type: string;
  is_pii: boolean;
  description: string | null;
};

export type ColumnRef = {
  column_id: string;
  table_id: string;
  schema_name: string;
  table_name: string;
  column_name: string;
};

export type Relationship = {
  id: string;
  relationship_type: string;
  from_column: ColumnRef;
  to_column: ColumnRef;
};

export type TableDetail = TableSummary & {
  columns: Column[];
  relationships: Relationship[];
};

export type SqlGrant = {
  id: string;
  user_id: string;
  granted_by: string;
  granted_at: string;
};

/** Just enough of `identity_service`'s `UserResponse` for the grant picker. */
export type TenantUser = {
  id: string;
  email: string;
  display_name: string;
  roles: string[];
};
