/** Mirrors `platform_contracts.AnalyticsRunEvent` (Section 11). Serialized
 * camelCase on the wire -- unlike every REST response in this app, which is
 * snake_case -- because it is the one contract with an explicit
 * `alias_generator=to_camel`. Do not "fix" the casing to match the rest. */
export type RunStage =
  | "intent"
  | "schema"
  | "semantic"
  | "sql"
  | "validation"
  | "execution"
  | "visualization"
  | "artifact"
  | "run";

export type RunEventStatus = "started" | "completed" | "failed" | "cancelled";

export type AnalyticsRunEvent = {
  runId: string;
  seq: number;
  stage: RunStage;
  status: RunEventStatus;
  message: string;
  artifactId?: string | null;
  createdAt: string;
};

/**
 * The chat screen's execution trace (Stitch "AI Analytics Chat") shows 6
 * steps, not Section 11's 9 stages -- adjacent stages that are one concept to
 * a user are grouped under a single label. `artifact` rides along with
 * `visualization` because a user never needs to distinguish "chart chosen"
 * from "chart saved as an artifact".
 */
export const TRACE_STEPS: ReadonlyArray<{ label: string; stages: readonly RunStage[] }> = [
  { label: "Understanding request", stages: ["intent"] },
  { label: "Finding relevant data", stages: ["schema", "semantic"] },
  { label: "Building query", stages: ["sql"] },
  { label: "Validating", stages: ["validation"] },
  { label: "Running query", stages: ["execution"] },
  { label: "Creating visualization", stages: ["visualization", "artifact"] },
];

/** Mirrors `dashboard_service.api.v1.schemas` (snake_case on the wire, unlike
 * `AnalyticsRunEvent` above). */
export type SourceRef = { data_source_id: string; tables: string[] };

export type ArtifactResponse = {
  artifact_id: string;
  title: string;
  summary: string;
  chart_spec: Record<string, unknown>;
  result_schema: Array<Record<string, unknown>>;
  source_refs: SourceRef[];
  refresh_policy: Record<string, unknown>;
  version: number;
  can_pin: boolean;
  conversation_id: string | null;
  run_id: string | null;
  created_at: string;
};

export type ResultColumn = { name: string; type: string };

export type ArtifactDataResponse = {
  artifact_id: string;
  columns: ResultColumn[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  expires_at: string;
};
