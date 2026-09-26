# 0025. A run can end with a reply; and the provider learns to read a reasoning model

## Status

Accepted. Closes the "live run" item ADR 0022 left open, and amends it: the provider it shipped
failed against the first real model it met.

## Context

Two reports, one screenshot: `hi` answered *"This kind of request is not supported yet."*, and a
valid retail question answered *"Something went wrong."*

**"Something went wrong" was never the model.** No `apps/analytics-orchestrator/.env` existed, so chat
was still on the offline `ScriptedProvider`, whose planner only handles a table with columns literally
named `amount`/`revenue`/`total`. The retail table has `net_amount`, so the stub raised a bare
`ProviderError` at `build_query_plan`, which is not one of the three typed provider errors and
therefore surfaced as `INTERNAL_ERROR`. The real LLM had not been asked.

**"Not supported" was a design gap, not a bug.** The real model classified `hi` correctly -- it is not
an analytics question. The product then turned "not analytics" into a *failed run*. A greeting is not a
failure; the assistant should answer it.

## Decision

### 1. Reading a reasoning model (the first real-LLM bug)

The configured server returns the model's chain of thought *inside `content`*, wrapped in
`<think>...</think>`, and that reasoning routinely quotes JSON. The provider extracted "first `{` to
last `}`", so it spanned the reasoning and the answer together -- invalid JSON, on every stage. It
passed 13 mock tests and would have failed every real request. Reproduced against the actual output
shape before fixing.

`_strip_reasoning` removes `<think>` blocks first. An *unterminated* block -- a reply cut off by
`max_tokens` mid-thought -- is removed to the end, leaving nothing to parse, so it fails validation as
the truncation it is instead of parsing a fragment of the model's musings. `completion_tokens` from
such servers already includes reasoning tokens, so the ledger charges what the server billed.

Live setup notes that mattered: both `minimax-m2.5` and `friday` are accepted (the server lists only
`friday`, so one is an alias); `ANALYTICS_LLM_MAX_TOKENS_PER_CALL` is raised to 8192 because reasoning
tokens count against it; the fallback model is set to the same model, since the default fallback
(`claude-opus-4-8`) does not exist on this server.

### 2. `conversation` intent

`AnalyticsRequest.intent` gains `conversation` and an optional `reply`, validated: a conversation must
carry a non-blank reply, and every reply is at most 300 characters of plain text (`_TEXT` forbids
markup and control characters; 300 is `AnalyticsRunEvent.message`'s limit). It is bounded because it is
shown verbatim.

One model call decides *and* replies -- the intent call already happens, so this costs no extra
tokens. `unsupported` remains, for when even a reply cannot be written.

### 3. How a run ends with only a reply

`AnalyticsRunState.reply` (default `None`, so persisted `flow_state` rows stay valid) is set by
`classify_intent`. From then on `run_step` returns immediately for every step except the two before the
decision, the one that made it, and `publish_events` -- no events, not marked complete. `publish_events`
closes the run as **completed**, with the reply as the closing `run.completed` message (that event is
the only place an artifact-less answer can travel) and stores it as the assistant message so the
conversation history has it.

Skipping via a predicate rather than raising an exception matters: CrewAI logs any exception out of a
listener at `ERROR`, so an exception-based early exit would log an error for every "hi".

The no-data-source check moved from the first step to schema retrieval. Otherwise a tenant that has
connected nothing yet would be told "No active data source is available" in reply to a greeting. It
still fails with `NO_DATA_SOURCE` -- the moment the run turns out to be a data question.

### What does not change

A conversational run never reaches a data source: no schema retrieval, no plan, no SQL, no
query-gateway call, no artifact -- asserted, not assumed (provider calls are exactly `[AnalyticsRequest]`,
query calls empty). The intent prompt still sees only the user's text, never catalogs or rows, so the
reply can leak no data. "Delete all the stores" is declined in words and nothing executes; the
read-only guarantee is enforced by query-gateway's validator, not by the reply.

## Verified live

Against the real model: `hi`, `what can you do?`, `thanks!` each answered in about three seconds;
`delete all the stores` politely declined with an offered alternative. Three real data questions still
complete after the prompt change, each routed without a selection: stores by country -> `retail-demo`
(pie), monthly net sales by store -> `retail-demo` (line), customers by country -> `sample-sales-db`
(bar). The pie chart's SQL was exactly `SELECT country, COUNT(store_id) ... FROM retail.stores GROUP BY
country`, and its summary matched the data.

## Consequences and caveats

- Tests: 4 provider tests reproducing the reasoning shapes, 6 integration tests for the reply path (4
  fail with the skip logic removed; the other two guard behaviour that must not change), 2 validation
  tests. The offline stub gained a small greeting matcher so those suites can exercise the path.
- **Reply wording is the model's**, so it is not deterministic and tests assert on structure (completed,
  no error text, the stored reply equals what is shown), not on words.
- **Latency is real.** A chart run takes 25-35s: six model calls, several with reasoning. That is a
  property of this model, not of the Flow.
- The Anthropic-specific path (`messages.parse`) is still live-untested; this closes only the
  OpenAI-compatible one.
