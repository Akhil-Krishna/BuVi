"""Export `platform_contracts` models as JSON Schema (Sections 17, 18.1).

`contracts/json-schema/` holds payload schemas (ChartSpec, AnalyticsRunEvent); `contracts/events/`
holds one schema per broker topic and major version. Run: `uv run python scripts/export_json_schemas.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from platform_contracts import (
    AnalyticsRunEvent,
    BillingUsageRecorded,
    ChartSpec,
    DashboardTilePinned,
    IdentityRoleChanged,
    McpInvocationDenied,
    MetadataSyncCompleted,
    RunRequested,
)


def schemas() -> dict[str, dict[str, Any]]:
    return {
        "contracts/json-schema/ChartSpec.json": ChartSpec.model_json_schema(by_alias=True),
        "contracts/json-schema/AnalyticsRunEvent.json": AnalyticsRunEvent.model_json_schema(
            by_alias=True
        ),
        "contracts/events/analytics.run.requested.v1.json": RunRequested.model_json_schema(),
        "contracts/events/billing.usage.recorded.v1.json": BillingUsageRecorded.model_json_schema(),
        "contracts/events/dashboard.tile.pinned.v1.json": DashboardTilePinned.model_json_schema(),
        "contracts/events/mcp.invocation.denied.v1.json": McpInvocationDenied.model_json_schema(),
        "contracts/events/metadata.sync.completed.v1.json": (
            MetadataSyncCompleted.model_json_schema()
        ),
        "contracts/events/identity.role.changed.v1.json": IdentityRoleChanged.model_json_schema(),
    }


def write(root: Path) -> None:
    for relative, schema in schemas().items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    write(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
