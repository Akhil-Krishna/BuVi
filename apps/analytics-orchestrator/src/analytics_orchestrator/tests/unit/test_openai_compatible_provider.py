"""The OpenAI-compatible provider's contract, offline (ADR 0022).

Every case a real server actually produces is asserted here against `httpx.MockTransport`, so the
provider is proven without a key or a network: a well-formed reply, a reply wrapped in a code
fence, a reply with prose around it, output that does not match the schema, truncation, a refusal,
a 429/5xx, and a 4xx. The live run against a real model is a separate, key-dependent check.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from analytics_orchestrator.application.services.ports import (
    ProviderOutputInvalidError,
    ProviderRefusedError,
    ProviderUnavailableError,
)
from analytics_orchestrator.infrastructure.llm.openai_compatible_provider import (
    OpenAICompatibleProvider,
)

pytestmark = pytest.mark.asyncio


class Answer(BaseModel):
    intent: str
    confidence: float


def _provider(handler: Any, mode: str = "json_schema") -> OpenAICompatibleProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://llm.test")
    return OpenAICompatibleProvider(
        base_url="http://llm.test/v1",
        api_key="test-key",
        timeout_seconds=5.0,
        response_format_mode=mode,
        client=client,
    )


def _reply(content: str, *, finish_reason: str = "stop", **extra: Any) -> httpx.Response:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    message.update(extra)
    return httpx.Response(
        200,
        json={
            "model": "minimax-m2.5",
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        },
    )


async def _generate(provider: OpenAICompatibleProvider) -> Any:
    return await provider.generate(
        model="minimax-m2.5",
        system="You classify intent.",
        user="How did revenue trend?",
        payload={},
        output_type=Answer,
        max_tokens=512,
    )


async def test_a_well_formed_reply_is_parsed_and_its_usage_reported() -> None:
    provider = _provider(lambda _r: _reply('{"intent":"trend","confidence":0.9}'))
    result = await _generate(provider)
    assert result.output == Answer(intent="trend", confidence=0.9)
    assert (result.input_tokens, result.output_tokens) == (120, 30)
    assert result.model == "minimax-m2.5"


async def test_the_schema_and_response_format_are_both_sent() -> None:
    """Layer 1 and 2 of the structured-output defence: a server that ignores `response_format`
    has still been told the schema in the system prompt."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _reply('{"intent":"trend","confidence":0.1}')

    await _generate(_provider(handler))
    assert seen["response_format"]["type"] == "json_schema"
    assert seen["response_format"]["json_schema"]["name"] == "Answer"
    assert "JSON Schema" in seen["messages"][0]["content"]
    assert "confidence" in seen["messages"][0]["content"]
    assert seen["max_tokens"] == 512


async def test_response_format_can_be_downgraded_or_omitted() -> None:
    for mode, expected in (("json_object", {"type": "json_object"}), ("none", None)):
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request, _seen: dict[str, Any] = seen) -> httpx.Response:
            _seen.update(json.loads(request.content))
            return _reply('{"intent":"trend","confidence":0.1}')

        await _generate(_provider(handler, mode=mode))
        assert seen.get("response_format") == expected, mode


async def test_a_fenced_or_chatty_reply_is_still_parsed() -> None:
    """Layer 3: a model that wraps JSON in a fence or prose is the normal case for many
    self-hosted models, and must not fail the run."""
    for content in (
        '```json\n{"intent":"trend","confidence":0.5}\n```',
        'Sure! Here you go:\n{"intent":"trend","confidence":0.5}\nHope that helps.',
        '```\n{"intent":"trend","confidence":0.5}\n```',
    ):
        result = await _generate(_provider(lambda _r, c=content: _reply(c)))
        assert result.output == Answer(intent="trend", confidence=0.5), content


async def test_output_off_schema_is_a_typed_invalid_error_with_its_usage() -> None:
    provider = _provider(lambda _r: _reply('{"intent":"trend"}'))  # confidence missing
    with pytest.raises(ProviderOutputInvalidError) as caught:
        await _generate(provider)
    assert (caught.value.input_tokens, caught.value.output_tokens) == (120, 30)


async def test_unparseable_output_is_invalid_not_a_crash() -> None:
    provider = _provider(lambda _r: _reply("I would rather describe it in words."))
    with pytest.raises(ProviderOutputInvalidError):
        await _generate(provider)


async def test_truncation_is_invalid_and_charged() -> None:
    provider = _provider(lambda _r: _reply('{"intent":"tr', finish_reason="length"))
    with pytest.raises(ProviderOutputInvalidError) as caught:
        await _generate(provider)
    assert caught.value.output_tokens == 30


async def test_a_content_filter_stop_is_a_refusal() -> None:
    provider = _provider(lambda _r: _reply("", finish_reason="content_filter"))
    with pytest.raises(ProviderRefusedError) as caught:
        await _generate(provider)
    assert caught.value.input_tokens == 120


async def test_an_explicit_refusal_field_is_a_refusal() -> None:
    provider = _provider(lambda _r: _reply("", refusal="I cannot help with that."))
    with pytest.raises(ProviderRefusedError):
        await _generate(provider)


async def test_rate_limit_and_server_errors_are_unavailable_so_the_router_may_fall_back() -> None:
    for status in (429, 500, 503):
        provider = _provider(lambda _r, s=status: httpx.Response(s, json={"error": "nope"}))
        with pytest.raises(ProviderUnavailableError):
            await _generate(provider)


async def test_a_transport_failure_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ProviderUnavailableError):
        await _generate(_provider(handler))


async def test_a_bad_request_is_not_retried_as_unavailable() -> None:
    """A 400 is this service's own request being wrong (bad model, unsupported response_format);
    treating it as transient would just burn the fallback model on the same mistake."""
    provider = _provider(lambda _r: httpx.Response(400, json={"error": "unknown model"}))
    with pytest.raises(ProviderOutputInvalidError):
        await _generate(provider)


async def test_a_malformed_envelope_is_invalid_not_a_crash() -> None:
    for body in ({"choices": []}, {"nope": True}, {"choices": [{"message": {}}]}):
        provider = _provider(lambda _r, b=body: httpx.Response(200, json=b))
        with pytest.raises(ProviderOutputInvalidError):
            await _generate(provider)
