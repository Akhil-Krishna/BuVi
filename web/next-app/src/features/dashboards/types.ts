/** Mirrors `dashboard_service.api.v1.schemas` (Section 16). All snake_case on
 * the wire -- unlike `AnalyticsRunEvent`. */
export type Dashboard = {
  id: string;
  name: string;
  visibility: "private" | "tenant";
  owner_id: string;
  is_owner: boolean;
  created_at: string;
  updated_at: string;
};

export type TilePosition = { x: number; y: number; w: number; h: number };

export type Tile = {
  id: string;
  dashboard_id: string;
  artifact_id: string;
  chart_spec_version: number;
  position: TilePosition;
  overrides: Record<string, unknown>;
  created_at: string;
};

export type DashboardDetail = Dashboard & { tiles: Tile[] };

export type ShareLink = {
  id: string;
  created_by: string;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  active: boolean;
};

export type ShareLinkCreated = ShareLink & { token: string; url: string };
