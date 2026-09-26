# 0024. A chat run picks its own data source when the caller names none -- and asks when unsure

## Status

Accepted. Changes `analytics-orchestrator`'s `load_context` / `retrieve_schema` steps; no contract,
endpoint, permission or SSE-sequence change.

## Context

Reported: chat failed with *"Choose which data source to use."* and there was no control to choose
one. Both halves were real.

1. **The UI never had a picker.** `ChatPanel` held `const [dataSourceId] = useState(...)` -- a state
   value with no setter and no control. The only way to set it was SQL Lab's `?dataSourceId=` handoff.
2. **The backend refused outright.** `_load_context` (the first step) raised
   `DATA_SOURCE_SELECTION_REQUIRED` whenever a tenant had more than one active source and the caller
   named none. So the moment a tenant connected a second database, every unqualified question failed.

The expectation is the reverse: with several active sources and no choice made, the flow should work
out which one the question is about.

## Decision

### Who picks changes; what is allowed does not

With more than one active source and none named, `_load_context` now leaves `data_source_id` unset
and `_retrieve_schema` chooses. The candidates are exactly the sources an explicit id would have been
accepted for -- the same `active_data_sources(tenant)` list -- and whatever is chosen goes through the
same schema retrieval, query-gateway validation and `authorize_query` as a named source. Nothing about
authorization is widened. An explicit `data_source_id` is never overridden.

### Chosen after intent, not before

Routing runs in `retrieve_schema`, after `classify_intent`, so the model's own reading of the request
(title, metrics, dimensions) contributes terms, not only the user's raw words. The step order and the
Section 32 SSE sequence are unchanged. The cost: an ambiguous run now fails *after* one intent call
rather than at the first step.

### The rule, and why it is biased toward asking

`choose_data_source` scores each source by its single best table (`table_score`, the same scorer
`rank_tables` uses: a table-name word is worth 3, a column word 1) and picks the leader only if it
beats the runner-up by `SOURCE_MARGIN = 2`. A tie, or a narrow lead, is **ambiguous** and the run asks;
nothing matching anywhere is **no match** and says so.

Asking is the deliberate default. A wrong guess answers from the wrong database and the chart looks
entirely plausible, so the error is silent. A wrong refusal costs one click. A source scores as its
*best* table, not its table count, so twenty weak tables cannot outvote one strong one. At most
`MAX_ROUTED_SOURCES = 10` sources are considered (each is a metadata call); past that the run asks.

### Plural stemming in the shared scorer

Measured against real catalogs, the first version failed on the question the data was built for:
*"monthly net sales by store"* scored 6 vs 5 -- a lead of 1 -- because a question saying `store` scored
**zero** against a table called `stores`; only exact words matched. Both sides of every comparison are
now folded (`stores`->`store`, `categories`->`category`). The stemmer is symmetric, so it only has to
be consistent, not right (`status`->`statu` on both sides still matches itself).

This changes `rank_tables` too, since they share `table_score` -- deliberately, as one scorer for both.

### The failure message

`DATA_SOURCE_SELECTION_REQUIRED` now says *"I could not tell which data source this is about. Name the
data or table you mean, or choose a data source."* The old text told a `client` to choose something
they have no control for.

### The picker

Chat gets a "Data source" `<select>` (default "Auto-detect from my question") for roles that can list
sources. `GET /data-sources` needs `data:manage`, which a `client` does not hold, so clients get no
picker and rely on routing. A failure to list degrades to auto-only rather than breaking chat.

## Measured, not assumed

Against the two real synced catalogs (`sample-sales-db`, `retail-demo`), eight realistic questions,
scored against what a person would answer. Six have a right source; two are genuinely unanswerable
without more information (*"how are we doing"*, *"show me monthly revenue"*) and should ask.

| | before stemming | after stemming |
|---|---|---|
| right source picked (of 6) | 3 | 4 |
| asked where a person could pick (of 6) | 3 | 2 |
| **picked the wrong source** | **0** | **0** |
| fully correct (of 8) | 5 | 6 |

Stemming is **not a pure win**: it gained *"monthly net sales by store"* and *"top selling items by
category"*, and **lost** *"revenue by region for Q2"* -- a correct pick became an ask, because folding
`sales` -> `sale` also raised `retail.sales`'s score against a sales-flavoured synonym expansion.
It is kept because it fixes a real structural defect (a singular question scored zero against a plural
table name), the net is positive, and the loss degrades to asking, not to a wrong answer.

The sales-flavoured synonym table (`revenue` -> `sales`, `order`, `amount`) is the remaining weakness:
it inflates whichever source looks sales-shaped. Tuning it further risks over-fitting two databases, so
this stops here.

## Consequences

Tests: 13 unit tests on the rule and stemming; 6 integration tests over HTTP (routes by question, routes
a different question elsewhere, explicit id never overridden, no match anywhere, identical sources still
tie and ask, **another tenant's perfectly-matching source is never a candidate**). Four of the six fail
with the executor reverted, so they are not vacuous; the other two guard behaviour that must *not*
change. A fake-gateway fix rides along: it reported a constant `sales.orders` regardless of the SQL,
which is why `authorize_query` (correctly) refused the first cross-catalog test -- it now reports the
tables in the SQL.

## What this does not solve

- **A `client` can still hit a dead end.** With two sources that both fit a question, a client is told
  to rephrase but cannot choose. Fixes need a decision, not a patch: a tenant default source (a new
  setting nobody has specified), or letting clients list source *names* (a permission change).
- **The lexical rule cannot resolve everything.** A model call to arbitrate genuinely ambiguous cases
  is the natural next step; it needs a new typed output, a scripted-provider answer for offline tests,
  and a decision about token accounting -- so it is not smuggled in here.
- Duplicate sources with identical schemas are always a tie, by design. Leftover `e2e-source-*`
  connections from the B3 browser spec caused exactly this in local testing and were removed.
