"""Claude via the official Anthropic SDK (Section 23: one provider/model behind the ModelRouter).

Structured output uses `messages.parse(output_format=<Pydantic model>)`, so a stage receives a
validated model or a typed failure. Refusals (`stop_reason == "refusal"`) and truncated or
unparseable output are reported with their token usage so the router can charge them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import anthropic

from analytics_orchestrator.application.services.ports import (
    OutputT,
    ProviderOutputInvalidError,
    ProviderRefusedError,
    ProviderResponse,
    ProviderUnavailableError,
)


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self, *, timeout_seconds: float, client: anthropic.AsyncAnthropic | None = None
    ) -> None:
        self._client = client or anthropic.AsyncAnthropic(timeout=timeout_seconds, max_retries=1)

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
        try:
            response = await self._client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=output_type,
            )
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError):
            raise ProviderUnavailableError() from None
        except anthropic.APIStatusError as error:
            if error.status_code >= 500:
                raise ProviderUnavailableError() from None
            raise ProviderOutputInvalidError() from None
        usage = response.usage
        if response.stop_reason == "refusal":
            raise ProviderRefusedError(usage.input_tokens, usage.output_tokens)
        parsed = response.parsed_output
        if response.stop_reason == "max_tokens" or not isinstance(parsed, output_type):
            raise ProviderOutputInvalidError(usage.input_tokens, usage.output_tokens)
        return ProviderResponse(
            output=parsed,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            model=response.model,
        )
