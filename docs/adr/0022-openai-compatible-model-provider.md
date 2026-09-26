# 0022. An OpenAI-compatible provider, so the Flow is proven against a real model

## Status

Accepted; the provider is built and unit-proven. **The live run is still outstanding** — see
"What this does not close". Amends, but does not delete, Phase C1's Anthropic entry requirement.

## Context

Phase C1's first entry requirement is that the `anthropic` provider be exercised against the real
Anthropic API with a real key. ADR 0006 recorded why it matters: every stage of the CrewAI Flow has
only ever run against `ScriptedProvider`, a deterministic offline stub. Nothing in Tracks A or B has
ever seen a model that rambles, wraps JSON in a code fence, refuses, or truncates mid-object — so
the Flow's structured-output handling, refusal path, `max_tokens` path and token accounting are all
untested against reality.

No Anthropic key is available for this project. What *is* available is a self-hosted
OpenAI-compatible server (a `/v1` base URL, a bearer key, model `minimax-m2.5`).

That endpoint does **not** satisfy the requirement as literally written — it is neither the
`anthropic` provider nor the Anthropic API. But the requirement's *purpose* is to stop shipping a
Flow whose only witnessed model is a stub, and a real model behind an OpenAI-compatible route
serves that purpose directly.

## Decision

Add a third provider, `openai_compatible`, behind the existing `ModelProvider` protocol, and treat
a live run against it as satisfying the **intent** of the entry requirement while leaving the
Anthropic-specific check open.

- `infrastructure/llm/openai_compatible_provider.py` implements the same protocol as
  `AnthropicProvider`, returning `ProviderResponse` or one of the three typed provider errors, so
  the ModelRouter's fallback, repair and budget logic is reached unchanged.
- Built on `httpx`, already a dependency of this service, rather than adding the `openai` SDK: the
  call is a single POST, and a new dependency in the analytics path is not worth the resolution risk.
- Configured by `llm_provider=openai_compatible`, `llm_base_url`, `llm_api_key` (a `SecretStr`) and
  `llm_response_format`.

### Structured output is defended in three layers

"OpenAI-compatible" is a family, not a contract — servers differ in whether `response_format`
is honoured at all. So:

1. `response_format` is requested, `json_schema` by default, downgradable to `json_object` or
   `none` by config;
2. the JSON Schema is **also** stated in the system prompt, so a server that silently ignores
   `response_format` has still been told what to produce;
3. the reply is extracted leniently (code fences, surrounding prose) and then validated by the
   Pydantic model. An off-schema or unparseable answer becomes `ProviderOutputInvalidError` with
   its token usage attached — never a half-parsed dict passed to the next stage, which Section 10.3
   forbids outright.

### Mapping a chat-completions reply onto the existing error contract

| Observed | Raised | Why |
|---|---|---|
| 429, 5xx, timeout, transport error | `ProviderUnavailableError` | Transient; the router may fall back. |
| `finish_reason: content_filter`, or a `refusal` field | `ProviderRefusedError` | The model declined; charged, and the router may fall back. |
| `finish_reason: length` | `ProviderOutputInvalidError` | Truncated, so possibly repairable — not a refusal. |
| Other 4xx | `ProviderOutputInvalidError` | This service's own request is wrong (bad model name, unsupported `response_format`). Retrying the same mistake on the fallback model would only burn budget. |
| Off-schema / unparseable body | `ProviderOutputInvalidError` | Counts as a repair attempt. |

### Production safety

`assert_production_safe` previously demanded `llm_provider == "anthropic"`, which would have
refused to boot with this provider. It now refuses only `scripted`, and additionally requires that
`openai_compatible` carry a base URL and a key, and that the base URL not be plaintext `http://` —
a model call carries tenant schema and the user's question, so it may not leave in clear. Note the
dev endpoint *is* plaintext http, which is fine for `environment=dev` and deliberately blocked for
staging/prod.

## Consequences

`tests/unit/test_openai_compatible_provider.py` proves the contract offline against
`httpx.MockTransport` — 13 cases, no key and no network: a clean reply; a fenced reply; a reply with
prose around the JSON; `response_format` and the schema instruction both being sent; the
`json_object`/`none` downgrades; off-schema output; unparseable output; truncation; a
`content_filter` refusal; an explicit `refusal` field; 429/500/503; a transport failure; a 400; and
four shapes of malformed envelope. Token usage is asserted to travel with the errors that carry it,
since the router charges them.

The secret is handled like every other: `llm_api_key` is a `SecretStr`, supplied by environment, and
`.gitignore` already covers `.env*`, so no key reaches Git (Section 37).

## What this does not close

- **The live run has not happened.** The key was not available when the provider was written, so
  what exists is a provider proven against a mock. Running the Phase A5 DoD message end to end
  against `minimax-m2.5` — primary and fallback model, structured output, a refusal, a `max_tokens`
  truncation, and `/billing/usage` matching the actual token counts — is the remaining step, and
  it is what this ADR should be amended with.
- **The Anthropic-specific requirement stays open**, narrowed rather than removed: the risk ADR
  0006 named (an unwitnessed Flow) is retired by a live run against any real model, but Anthropic's
  own SDK path — `messages.parse`, its `stop_reason` values, its usage fields — is only exercised by
  `AnthropicProvider`, which remains live-untested. That matters the moment a deployment points at
  Anthropic.
- Model quality is explicitly **not** a claim here. Whether `minimax-m2.5` produces good SQL plans
  for this Flow is a separate question from whether the provider integration is correct.
