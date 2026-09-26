"""Any OpenAI-compatible `/chat/completions` endpoint behind the same ModelRouter (Section 23).

This exists so the Flow can be exercised against a *real* model without an Anthropic key -- a
self-hosted or gateway-fronted server (vLLM, SGLang, LiteLLM, Ollama, a vendor's OpenAI-compatible
route) speaks this shape. ADR 0022 records why that matters: until Phase C1, every stage had only
ever seen `ScriptedProvider`, so nothing had proven the Flow against a model that can ramble,
refuse, truncate, or wrap JSON in prose.

Deliberately built on `httpx`, which this service already depends on, rather than adding the
`openai` SDK: the call is one POST, and a new dependency in the analytics path is not worth it.

Structured output is defended in three layers, because "OpenAI-compatible" servers vary in what
they actually implement:

1. `response_format` is requested (`json_schema` by default, downgradable by config);
2. the schema is *also* stated in the system prompt, so a server that silently ignores
   `response_format` still gets told what to produce;
3. the response body is extracted leniently (fenced code blocks, leading prose) and then validated
   by the Pydantic model -- an unparseable or off-schema answer is a typed
   `ProviderOutputInvalidError`, never a half-parsed dict handed to the next stage (Section 10.3).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any, Final

import httpx
from pydantic import ValidationError

from analytics_orchestrator.application.services.ports import (
    OutputT,
    ProviderOutputInvalidError,
    ProviderRefusedError,
    ProviderResponse,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)

#: `finish_reason` values that mean the model declined rather than failed (Section 23: a refusal
#: is reported with its usage so the router can charge it and fall back).
_REFUSAL_REASONS: Final = frozenset({"content_filter"})

#: Truncated output is not a refusal -- it is an invalid answer that may be worth repairing.
_TRUNCATION_REASONS: Final = frozenset({"length"})

ResponseFormatMode = str


def _extract_json_object(content: str) -> str:
    """Pull the JSON object out of a reply that may be fenced or prefaced with prose.

    Kept deliberately simple: strip a ```json fence if present, otherwise take the outermost
    brace-balanced span. Anything else is left to fail Pydantic validation, which is the real gate.
    """
    text = content.strip()
    if text.startswith("```"):
        # ```json\n{...}\n``` -> {...}
        body = text.split("```", 2)
        if len(body) >= 2:
            fenced = body[1]
            if fenced.lower().startswith("json"):
                fenced = fenced[4:]
            text = fenced.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start : end + 1]


def _schema_instruction(output_type: type[OutputT]) -> str:
    schema = json.dumps(output_type.model_json_schema(), separators=(",", ":"))
    return (
        "Reply with a single JSON object and nothing else -- no prose, no code fence. "
        f"It must validate against this JSON Schema: {schema}"
    )


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        response_format_mode: ResponseFormatMode = "json_schema",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._response_format_mode = response_format_mode
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
            headers={"authorization": f"Bearer {api_key}"},
            follow_redirects=False,
        )

    def _response_format(self, output_type: type[OutputT]) -> dict[str, Any] | None:
        if self._response_format_mode == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": output_type.__name__,
                    "schema": output_type.model_json_schema(),
                    "strict": True,
                },
            }
        if self._response_format_mode == "json_object":
            return {"type": "json_object"}
        return None

    async def generate(
        self,
        *,
        model: str,
        system: str,
        user: str,
        payload: Mapping[str, Any],  # noqa: ARG002 - the rendered `user` already carries it
        output_type: type[OutputT],
        max_tokens: int,
    ) -> ProviderResponse[OutputT]:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": f"{system}\n\n{_schema_instruction(output_type)}"},
                {"role": "user", "content": user},
            ],
        }
        response_format = self._response_format(output_type)
        if response_format is not None:
            body["response_format"] = response_format

        try:
            http_response = await self._client.post(f"{self._base_url}/chat/completions", json=body)
        except (httpx.TimeoutException, httpx.TransportError):
            raise ProviderUnavailableError() from None

        if http_response.status_code == 429 or http_response.status_code >= 500:
            raise ProviderUnavailableError() from None
        if http_response.status_code >= 400:
            # A 4xx here is this service's own request being wrong (bad model name, unsupported
            # response_format). Not retryable against the same provider, and never surfaced
            # upstream as provider text -- the router decides what the run sees.
            logger.warning(
                "openai-compatible provider refused the request",
                extra={"status": http_response.status_code},
            )
            raise ProviderOutputInvalidError() from None

        try:
            envelope = http_response.json()
            choice = envelope["choices"][0]
            content = choice["message"]["content"] or ""
            usage = envelope.get("usage") or {}
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            raise ProviderOutputInvalidError() from None

        input_tokens = int(usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(usage.get("completion_tokens", 0) or 0)
        finish_reason = choice.get("finish_reason") or ""

        # Some servers put a declined answer in a dedicated field rather than a finish_reason.
        if finish_reason in _REFUSAL_REASONS or choice["message"].get("refusal"):
            raise ProviderRefusedError(input_tokens, output_tokens)
        if finish_reason in _TRUNCATION_REASONS:
            raise ProviderOutputInvalidError(input_tokens, output_tokens)

        try:
            parsed = output_type.model_validate_json(_extract_json_object(content))
        except ValidationError:
            raise ProviderOutputInvalidError(input_tokens, output_tokens) from None

        return ProviderResponse(
            output=parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=str(envelope.get("model") or model),
        )
