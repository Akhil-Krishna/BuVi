import { ChartRenderer } from "@/features/charts/ChartRenderer";
import { parseChartSpec } from "@/features/charts/types";
import type { SnapshotTile } from "./types";

const GRID_COLUMNS = 12; // dashboard_service.domain.policies.dashboard_policy.GRID_COLUMNS
const ROW_UNIT_PX = 24;

/** Same 12-column grid as `TileGrid` (B2), but the snapshot response already
 * carries each tile's `chart_spec`/`data` inline -- no per-tile artifact
 * fetch, since a guest has no session to fetch with. */
export function SnapshotGrid({ tiles }: { tiles: SnapshotTile[] }) {
  if (tiles.length === 0) {
    return <p className="mt-6 text-sm text-text-secondary">This dashboard has no tiles.</p>;
  }

  return (
    <div
      className="mt-6 grid gap-4"
      style={{ gridTemplateColumns: `repeat(${GRID_COLUMNS}, minmax(0, 1fr))` }}
    >
      {tiles.map((tile, i) => {
        const spec = parseChartSpec(tile.chart_spec);
        return (
          <div
            key={i}
            className="border border-border bg-bg p-3"
            style={{
              gridColumn: `${tile.position.x + 1} / span ${tile.position.w}`,
              minHeight: tile.position.h * ROW_UNIT_PX,
            }}
          >
            <p className="text-sm font-medium text-text-primary">{tile.title}</p>
            {tile.data_status === "ok" && spec && tile.data ? (
              <div className="mt-2">
                <ChartRenderer
                  spec={spec}
                  columns={tile.data.columns}
                  rows={tile.data.rows}
                  height={Math.max(tile.position.h * ROW_UNIT_PX - 48, 160)}
                />
              </div>
            ) : (
              <p className="mt-2 text-sm text-text-secondary">
                {tile.data_status === "too_large"
                  ? "This result is too large to preview in a shared link."
                  : "This chart's data has expired."}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
