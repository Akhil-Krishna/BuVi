"""Shared, versioned wire contracts (Sections 11, 17, 18.1).

Contract: `ChartSpec` and its parts, `ResultField`, `AnalyticsRunEvent`, `RunRequested`,
`BillingUsageRecorded`, `USAGE_METRICS`, `DashboardTilePinned`, `McpInvocationDenied`,
`MetadataSyncCompleted`, `IdentityRoleChanged`, `SchemaVersionError`.
Nothing here imports a service.
"""

from platform_contracts.analytics import (
    RUN_STAGES,
    USAGE_METRICS,
    AnalyticsRunEvent,
    BillingUsageRecorded,
    RunRequested,
    SchemaVersionError,
)
from platform_contracts.chart_spec import (
    ChartEncodings,
    ChartOptions,
    ChartSpec,
    ChartType,
    Encoding,
    FieldType,
    ResultField,
)
from platform_contracts.dashboard import DashboardTilePinned
from platform_contracts.identity import IdentityRoleChanged
from platform_contracts.mcp import McpInvocationDenied
from platform_contracts.metadata import MetadataSyncCompleted

__all__ = [
    "RUN_STAGES",
    "USAGE_METRICS",
    "AnalyticsRunEvent",
    "BillingUsageRecorded",
    "ChartEncodings",
    "ChartOptions",
    "ChartSpec",
    "ChartType",
    "DashboardTilePinned",
    "Encoding",
    "FieldType",
    "IdentityRoleChanged",
    "McpInvocationDenied",
    "MetadataSyncCompleted",
    "ResultField",
    "RunRequested",
    "SchemaVersionError",
]
