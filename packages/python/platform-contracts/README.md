# platform-contracts

Shared, versioned wire contracts (build spec Sections 11, 17, 18.1). Producer and consumer import
the same model; JSON Schemas under `contracts/json-schema/` and `contracts/events/` are generated
from these models (`scripts/export_json_schemas.py`) and checked for drift in CI.

Contract:

- `ChartSpec`, `ResultField` -- Section 17's bounded, strict chart vocabulary (`extra="forbid"`,
  one spelling per key, no coercion). Checking a spec against a result schema is
  visualization-service's validator, not this package's.
- `AnalyticsRunEvent` -- Section 11's user-safe run event, serialized camelCase for SSE.
- `RunRequested` (`analytics.run.requested`), `BillingUsageRecorded` (`billing.usage.recorded`) --
  Section 18.1 event payloads with a `schema_version`; consumers reject unknown major versions.
  `BillingUsageRecorded` is 1.1 (Phase A11): `event_id` (the aggregator's idempotency key),
  `occurred_at`, and `query_execution_ms` from query-gateway, which has no `model`.
- `DashboardTilePinned` (`dashboard.tile.pinned`), `McpInvocationDenied`
  (`mcp.invocation.denied`), `MetadataSyncCompleted` (`metadata.sync.completed`) and
  `IdentityRoleChanged` (`identity.role.changed`), all consumed by notification-service (A11).

Nothing here imports a service.
