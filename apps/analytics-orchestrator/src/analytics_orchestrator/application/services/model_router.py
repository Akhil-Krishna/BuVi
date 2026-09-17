"""The ModelRouter (Section 23): the only way any stage reaches a model.

Before every call it enforces the per-run token cap and the tenant's daily cap on a conservative
estimate (prompt + `max_tokens`); after every call it charges actual usage and re-checks the run
cap, so a run can never finish past its budget. Output that fails schema or semantic validation
gets a bounded repair loop (Section 10.3: at most 2 repairs). Errors, timeouts and refusals fall
back to the configured fallback model once -- never a silent downgrade for cost.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from analytics_orchestrator.application.services.ports import (
    ModelProvider,
    OutputT,
    ProviderOutputInvalidError,
    ProviderRefusedError,
    ProviderUnavailableError,
    TokenLedger,
    UsageSink,
)
from analytics_orchestrator.application.services.prompts import estimate_tokens, render_user
from analytics_orchestrator.domain.value_objects.failures import FailureCode, RunFailedError
from analytics_orchestrator.domain.value_objects.run_state import AnalyticsRunState
from platform_contracts import BillingUsageRecorded
from platform_observability import request_id_var

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelRoute:
    provider: ModelProvider
    model: str


@dataclass(frozen=True)
class BudgetLimits:
    run_tokens: int
    tenant_daily_tokens: int
    max_tokens_per_call: int
    max_repairs: int


class ModelRouter:
    def __init__(
        self,
        *,
        primary: ModelRoute,
        fallback: ModelRoute | None,
        ledger: TokenLedger,
        usage: UsageSink,
        limits: BudgetLimits,
    ) -> None:
        self._routes = [primary] + ([fallback] if fallback else [])
        self._ledger = ledger
        self._usage = usage
        self._limits = limits

    async def generate(
        self,
        state: AnalyticsRunState,
        *,
        stage: str,
        system: str,
        payload: Mapping[str, Any],
        output_type: type[OutputT],
        check: Callable[[OutputT], list[str]] | None = None,
        exhausted: FailureCode = FailureCode.OUTPUT_INVALID,
    ) -> OutputT:
        attempt_payload = dict(payload)
        for attempt in range(self._limits.max_repairs + 1):
            output = await self._call(state, stage, system, attempt_payload, output_type)
            problems = (
                ["output did not match the required schema"]
                if output is None
                else (check(output) if check else [])
            )
            if output is not None and not problems:
                return output
            if attempt < self._limits.max_repairs:
                attempt_payload = {**payload, "previous_problems": problems[:10]}
        raise RunFailedError(exhausted)

    async def _call(
        self,
        state: AnalyticsRunState,
        stage: str,
        system: str,
        payload: Mapping[str, Any],
        output_type: type[OutputT],
    ) -> OutputT | None:
        user = render_user(payload)
        max_tokens = self._limits.max_tokens_per_call
        estimate = estimate_tokens(system, user) + max_tokens
        if state.usage.total + estimate > self._limits.run_tokens:
            raise RunFailedError(FailureCode.RUN_BUDGET_EXCEEDED)
        try:
            used_today = await self._ledger.used_today(state.tenant_id)
        except Exception:
            # Fail closed: an unenforceable budget is not a budget (Section 23).
            raise RunFailedError(FailureCode.BUDGET_UNAVAILABLE) from None
        if used_today + estimate > self._limits.tenant_daily_tokens:
            raise RunFailedError(FailureCode.TENANT_BUDGET_EXCEEDED)

        refused = False
        for route in self._routes:
            try:
                response = await route.provider.generate(
                    model=route.model,
                    system=system,
                    user=user,
                    payload=payload,
                    output_type=output_type,
                    max_tokens=max_tokens,
                )
            except ProviderOutputInvalidError as error:
                await self._charge(
                    state, stage, route.model, error.input_tokens, error.output_tokens
                )
                return None
            except ProviderRefusedError as error:
                await self._charge(
                    state, stage, route.model, error.input_tokens, error.output_tokens
                )
                refused = True
                continue
            except ProviderUnavailableError:
                logger.warning(
                    "model route unavailable",
                    extra={"context": {"stage": stage, "model": route.model}},
                )
                continue
            await self._charge(
                state, stage, response.model, response.input_tokens, response.output_tokens
            )
            return response.output
        raise RunFailedError(
            FailureCode.MODEL_REFUSED if refused else FailureCode.MODEL_UNAVAILABLE
        )

    async def _charge(
        self,
        state: AnalyticsRunState,
        stage: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        state.usage.input_tokens += input_tokens
        state.usage.output_tokens += output_tokens
        state.usage.calls += 1
        try:
            await self._ledger.charge(state.tenant_id, input_tokens + output_tokens)
        except Exception:
            logger.error("token ledger charge failed", extra={"context": {"stage": stage}})
        for metric, quantity in (
            ("llm_input_tokens", input_tokens),
            ("llm_output_tokens", output_tokens),
        ):
            try:
                await self._usage.record(
                    BillingUsageRecorded(
                        tenant_id=state.tenant_id,  # type: ignore[arg-type]
                        metric=metric,  # type: ignore[arg-type]
                        quantity=quantity,
                        run_id=state.id,  # type: ignore[arg-type]
                        stage=stage,
                        model=model,
                        request_id=request_id_var.get(),
                    )
                )
            except Exception:
                logger.warning(
                    "usage event not recorded",
                    extra={"context": {"stage": stage, "metric": metric}},
                )
        if state.usage.total > self._limits.run_tokens:
            raise RunFailedError(FailureCode.RUN_BUDGET_EXCEEDED)
