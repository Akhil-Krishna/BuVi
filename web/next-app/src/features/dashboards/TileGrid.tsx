import { getArtifact, getArtifactData } from "@/features/analytics/actions";
import { ChartRenderer } from "@/features/charts/ChartRenderer";
import { parseChartSpec } from "@/features/charts/types";
import type { Tile } from "./types";

const GRID_COLUMNS = 12; // dashboard_service.domain.policies.dashboard_policy.GRID_COLUMNS
const ROW_UNIT_PX = 24;

/** Resolves each tile's artifact + result data server-side (the tile only
 * stores `artifact_id`; Section 16's `chart_spec_version` is the tile's own
 * pinned version, but B2 always renders the artifact's current chart_spec --
 * versioned tile overrides are a B-later refinement, not part of this DoD)
 * and lays them out on the same 12-column grid `dashboard-service` positions
 * them on, read-only (no drag/resize -- not required by Section 31's DoD). */
export async function TileGrid({ tiles }: { tiles: Tile[] }) {
  if (tiles.length === 0) {
    return (
      <p className="mt-6 text-sm text-text-secondary">
        No tiles yet. Pin a chart from Chat to add one.
      </p>
    );
  }

  const resolved = await Promise.all(
    tiles.map(async (tile) => {
      const [artifact, data] = await Promise.all([
        getArtifact(tile.artifact_id),
        getArtifactData(tile.artifact_id),
      ]);
      return {
        tile,
        artifact: artifact.ok ? artifact.data : null,
        data: data.ok ? data.data : null,
      };
    })
  );

  return (
    <div
      className="mt-6 grid gap-4"
      style={{ gridTemplateColumns: `repeat(${GRID_COLUMNS}, minmax(0, 1fr))` }}
    >
      {resolved.map(({ tile, artifact, data }) => {
        const spec = artifact ? parseChartSpec(artifact.chart_spec) : null;
        return (
          <div
            key={tile.id}
            className="border border-border bg-bg p-3"
            style={{
              gridColumn: `${tile.position.x + 1} / span ${tile.position.w}`,
              minHeight: tile.position.h * ROW_UNIT_PX,
            }}
          >
            {artifact ? (
              <>
                <p className="text-sm font-medium text-text-primary">{artifact.title}</p>
                {spec && data ? (
                  <div className="mt-2">
                    <ChartRenderer
                      spec={spec}
                      columns={data.columns}
                      rows={data.rows}
                      height={Math.max(tile.position.h * ROW_UNIT_PX - 48, 160)}
                    />
                  </div>
                ) : (
                  <p className="mt-2 text-sm text-text-secondary">
                    This chart could not be rendered.
                  </p>
                )}
              </>
            ) : (
              <p className="text-sm text-text-secondary">This artifact is no longer available.</p>
            )}
          </div>
        );
      })}
    </div>
  );
}
