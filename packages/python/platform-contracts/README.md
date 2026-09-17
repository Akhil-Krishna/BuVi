# platform-contracts

Shared, versioned wire contracts (build spec Sections 11, 17, 18.1). Producer and consumer import
the same model; JSON Schemas under `contracts/json-schema/` and `contracts/events/` are generated
from these models (`scripts/export_json_schemas.py`) and checked for drift in CI.

Contract:

- `ChartSpec`, `ResultField`, `validate_chart_spec(spec, result_schema)` -- Section 17's bounded,
  strict chart vocabulary (`extra="forbid"` everywhere; encodings must reference result fields).
- `AnalyticsRunEvent` -- Section 11's user-safe run event, serialized camelCase for SSE.
- `RunRequested` (`analytics.run.requested`), `BillingUsageRecorded` (`billing.usage.recorded`) --
  Section 18.1 event payloads with a `schema_version`; consumers reject unknown major versions.

Nothing here imports a service.
