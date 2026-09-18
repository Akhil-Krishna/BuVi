# 0009 — What result data `analyze_result` may show a model

- **Status:** Accepted · **Date:** 2026-09-18 · **Phase:** A7 (decided before implementation, as the spec requires)

## Context

`analyze_result` is the first Flow stage that exposes query *results* to the model provider. Every earlier stage sees the request, catalog metadata and plans, never customer rows. The spec offered three options: the result schema, aggregates, or capped rows.

## Decision: deterministic aggregates only; never rows, never categorical values

1. **Input to the model** is a `ResultStats` block that the orchestrator computes itself from the rows query-gateway returns. It never contains a row. Per column:
   - **quantitative:** `min`, `max`, `sum`, `mean` (rounded to 4 significant decimals), `count`;
   - **temporal:** `min`, `max`;
   - **nominal/ordinal:** `distinct_count` only. Category values such as region names are never sent.

   Plus `row_count`, `truncated` and the result schema. The block is rendered as tagged data, like every other input (§10.3).
2. **Rows are never persisted.** Stats are computed in memory from the execute response. `flow_state` stores the stats, not the rows; the rows stay behind query-gateway's TTL-bound handle.
3. **Output** is a typed `ResultInsight`: a `headline` of at most 200 characters and at most 3 `observations` of at most 160 characters each, all plain text with no markup.
4. **Grounding check (deterministic).** Every number in the insight must be one of:
   - a value from `ResultStats`, compared at the precision it is written;
   - the row count;
   - a number from the user's own request, such as the year "2026" (digits inside a word such as "Q2" are not numbers).

   Anything else, for example a derived "12% growth", is reported back through the bounded repair loop (≤2). A number is compared at the precision it is written, rounding half-up: "2,181" is grounded by a sum of 2180.5.
5. **Non-fatal.** If the insight stays ungrounded or invalid after repairs, or the model is unavailable or refuses, the run continues with the deterministic summary (`"<title> — N rows"`). Budget failures (`RUN_BUDGET_EXCEEDED`, `TENANT_BUDGET_EXCEEDED`, `BUDGET_UNAVAILABLE`) still fail the run: they are the enforcement, not an insight problem.
6. **Where it shows.** The grounded headline becomes the artifact `summary` and the assistant message. Whether it was grounded is recorded in the run's grounding record (Phase A7 groundedness eval).

## Why not the alternatives

- **Result schema only:** too little to say anything useful ("3 rows of revenue by month").
- **Capped rows:** sends customer records, including categorical values that may be sensitive even when not PII-tagged, to a third-party model. The provider would then need to be treated as a data processor for row-level data, a tenant-policy question this platform has not settled. Aggregates give the model enough for a headline and keep row-level data inside our boundary.

## Consequences

- Insights cannot name the top category ("West led revenue") because category values are not sent. Adding that needs a tenant-level opt-in and a DPA decision; it is out of scope until then.
- Numeric aggregates of sensitive columns are still data. Only agent-visible, non-PII columns reach a result in the first place (§12), so this adds no new column exposure.
