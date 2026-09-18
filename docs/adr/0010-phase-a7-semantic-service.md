# 0010 — Phase A7: semantic-service, `resolve_semantics`, `analyze_result`

- **Status:** Accepted · **Date:** 2026-09-18 · **Phase:** A7
- **Related:** spec commit 1a6283f (plan fixes); [ADR 0009](0009-analyze-result-data-exposure.md) (what `analyze_result` may see).

## Plan issues found before implementation (fixed in the spec)

| Issue | Resolution |
|---|---|
| `metrics.expression` was free SQL text (e.g. `SUM(orders.amount)`) that would flow into generated queries, a stored-injection channel | v1 grammar: `SUM\|AVG\|MIN\|MAX\|COUNT([DISTINCT] column)` over the base table. It is parsed, checked against the catalog (exists, not PII, table agent-visible, numeric for SUM/AVG) and stored normalized. Ratios, filters and multi-table metrics wait for `join_rules` and a later grammar. |
| No approval route, but only approved metrics should ground the Flow; `approved_by` existed with no workflow | `POST /semantic/metrics/{id}/approve` (draft → approved) and `…/deprecate` (approved → deprecated). Approval re-checks the catalog. Create, approve and deprecate are audited through identity-service (`semantic.` prefix). DDL gains `created_by` and `approved_at`. |
| "Map to approved metrics/dimensions" had no dimension routes; the DDL columns were nullable | `GET/POST /semantic/dimensions`; `base_table_id` and `column_id` are NOT NULL. |
| The Flow could not match a metric's `base_table_id` to the agent context, which carried no ids | metadata-service adds `id` to context tables and columns (additive), and `POST /internal/v1/catalog/lookup` (scope `metadata-service:catalog-lookup`) for semantic-service's write-time checks. |
| §32 had no `semantic` events although §11 defines the stage | `semantic.started/completed` sit between schema and sql. `analyze_result` runs inside `visualization` (no new stage). |
| `retrieve_schema` keeps the top-ranked tables, so a metric's base table could be dropped | `resolve_semantics` adds the base table of a resolved definition to the context, but only if it is in the permitted packet. |
| `analyze_result` needed rows the Flow never kept | Aggregates are computed where the execute response is read; rows go no further (ADR 0009). |

## Decisions

1. **semantic-service** (port 8008, schema `semantic`, RLS):
   - The request role cannot delete definitions, so artifacts that reference one stay explainable.
   - `GET /internal/v1/semantic-context` (scope `semantic-service:context`, caller analytics-orchestrator) serves *approved* metrics only, with the expression already parsed into aggregation and column, plus dimensions.
2. **Grounding is deterministic; the model only chooses.**
   - `resolve_semantics` offers the model the approved candidates that fall inside the permitted context packet. A definition on a hidden table, a PII column or another data source is never offered, so it cannot widen access.
   - The model answers with ids, which are checked against the candidates.
   - A resolved metric fixes the measure: aggregation, column and alias come from the definition. The plan must contain that exact measure, alias included.
   - The SQL query-gateway validated is **parsed** (sqlglot, the same parser and version range as query-gateway). The output column named by the metric's alias must be exactly `AGG([DISTINCT] column)` on the metric's base table: no arithmetic, `FILTER`, window, `CASE`, other column or duplicate alias.
   - Unparseable SQL, or anything that is not a single `SELECT`, is a problem too.
   - **Fail-closed path:** a mismatch goes through the existing ≤2 SQL repairs, and one that survives them fails the run `QUERY_REJECTED` (`validation.failed`), with nothing executed and no artifact. `test_sql_that_keeps_modifying_the_metric_fails_closed` covers it.
   - With no approved definition in scope there is no model call.
3. **Fail closed.** If semantic-service is unavailable the run fails `UPSTREAM_UNAVAILABLE` (`semantic.failed`) rather than guessing a metric that may be defined differently.
4. **Groundedness per run.** `flow_state.grounding` records:
   - metric ids and names;
   - dimension ids;
   - unmatched terms;
   - measures from metrics out of total measures;
   - whether the insight was grounded or fell back.

   The artifact's `semantic_query` carries the metrics and dimensions used. The eval corpus `tests/integration/test_groundedness_eval.py` (`make eval-groundedness`) reports this per run and fails below 100% on the scripted provider; pointed at a real model it becomes the prompt regression eval.
5. **`analyze_result`** follows ADR 0009: aggregates only, every number grounded (half-up at the written precision), non-fatal fallback. The grounded headline becomes the artifact summary and the assistant message.
6. **Bugs fixed on the way.**
   - The scripted SQL generator always wrote `sum(...)` whatever the plan's aggregation.
   - Insight grounding initially used banker's rounding, so 2180.5 did not ground "2,181".
   - **The first SQL check was presence-based** (a regex for `AGG(column)` anywhere in the SQL). It silently accepted `SUM(x) * 2 AS revenue`, `SUM(x) FILTER (…)`, `SUM(x) OVER ()`, and the metric computed under another alias while the metric's alias came from a different column. It was replaced by the parsed exact check above (review follow-up after commit 7380996); the plan check now also pins the alias.

## Gaps

- **Only single-aggregate, single-table metrics.** Ratios (AOV as revenue ÷ orders), metric filters and approved joins (`join_rules` is modeled, with no routes) are **post-GA backlog** (spec "Post-GA backlog"; CLAUDE.md "Carried forward").
- **Dimensions are create/list only.** Edits and deletion follow B4's management UI needs.
- **No four-eyes rule.** A creator may approve their own metric; separation of duties is a tenant policy for Phase A10.
- **Semantic context is not cached** (§20 asks for TTL caching). Each run makes one extra metadata context call when approved definitions exist. This is a **Phase C1 hardening requirement** (spec Phase C1), not an open-ended gap.
