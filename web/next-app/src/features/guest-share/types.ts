export type SnapshotTile = {
  title: string;
  position: { x: number; y: number; w: number; h: number };
  chart_spec: Record<string, unknown>;
  overrides: Record<string, unknown>;
  data: { columns: { name: string; type: string }[]; rows: unknown[][] } | null;
  data_status: "ok" | "expired" | "too_large";
};

export type Snapshot = {
  name: string;
  expires_at: string;
  tiles: SnapshotTile[];
};
