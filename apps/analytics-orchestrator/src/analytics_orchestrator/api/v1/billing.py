"""Billing reads (Sections 9, 23).

* `GET /billing/quotas` (Phase A10): the tenant's LLM token budget for today. The ModelRouter
  enforces this budget before every model call; this reads the same ledger, so the number shown
  is the number enforced.
* `GET /billing/usage` (Phase A11): metered usage over a period, summed from
  `analytics.usage_records` (written by worker-runtime from `billing.usage.recorded`), plus the
  current seat count from identity-service.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from redis.exceptions import RedisError

from analytics_orchestrator.application.services.ports import DependencyUnavailableError
from analytics_orchestrator.dependencies import ScopedRepo
from analytics_orchestrator.domain.errors import UpstreamUnavailableError, ValidationFailedError
from platform_auth import Principal, require_permission
from platform_auth.permissions import PERM_BILLING_READ

router = APIRouter(tags=["billing"])

BillingRead = Annotated[Principal, Depends(require_permission(PERM_BILLING_READ))]


class TokenQuota(BaseModel):
    period: Literal["day"]
    limit: int
    used: int
    remaining: int
    resets_at: dt.datetime


class QuotasResponse(BaseModel):
    llm_tokens: TokenQuota
    #: Per-run cap (Section 10.3): one request cannot spend more than this.
    run_token_limit: int


@router.get("/billing/quotas", response_model=QuotasResponse)
async def quotas(request: Request, principal: BillingRead) -> QuotasResponse:
    settings = request.app.state.settings
    try:
        used = await request.app.state.token_ledger.used_today(str(uuid.UUID(principal.tenant_id)))
    except RedisError:
        raise UpstreamUnavailableError() from None
    limit = int(settings.tenant_daily_token_budget)
    now = dt.datetime.now(dt.UTC)
    midnight = dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time(), tzinfo=dt.UTC)
    return QuotasResponse(
        llm_tokens=TokenQuota(
            period="day",
            limit=limit,
            used=used,
            remaining=max(0, limit - used),
            resets_at=midnight,
        ),
        run_token_limit=int(settings.run_token_budget),
    )


MAX_USAGE_DAYS = 366


class LlmTokenUsage(BaseModel):
    input: int
    output: int
    total: int
    #: Input plus output per Flow stage.
    by_stage: dict[str, int]


class UsageResponse(BaseModel):
    #: Inclusive UTC dates.
    start: dt.date
    end: dt.date
    llm_tokens: LlmTokenUsage
    query_minutes: float
    #: Active users now, not over the period.
    seats: int


@router.get("/billing/usage", response_model=UsageResponse)
async def usage(
    request: Request,
    principal: BillingRead,
    repository: ScopedRepo,
    start: Annotated[dt.date | None, Query()] = None,
    end: Annotated[dt.date | None, Query()] = None,
) -> UsageResponse:
    """Default period: the current UTC month to date."""
    today = dt.datetime.now(dt.UTC).date()
    last = end or today
    first = start or last.replace(day=1)
    if first > last or (last - first).days >= MAX_USAGE_DAYS:
        raise ValidationFailedError(
            "The period must start on or before its end and span at most 366 days."
        )
    tenant_id = uuid.UUID(principal.tenant_id)
    totals = await repository.usage_totals(
        tenant_id,
        dt.datetime.combine(first, dt.time(), tzinfo=dt.UTC),
        dt.datetime.combine(last + dt.timedelta(days=1), dt.time(), tzinfo=dt.UTC),
    )
    by_metric: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    for metric, stage, total in totals:
        by_metric[metric] = by_metric.get(metric, 0) + total
        if metric in ("llm_input_tokens", "llm_output_tokens"):
            key = stage or "unattributed"
            by_stage[key] = by_stage.get(key, 0) + total
    try:
        seats = await request.app.state.identity.active_seats(tenant_id)
    except DependencyUnavailableError:
        raise UpstreamUnavailableError() from None
    tokens_in = by_metric.get("llm_input_tokens", 0)
    tokens_out = by_metric.get("llm_output_tokens", 0)
    return UsageResponse(
        start=first,
        end=last,
        llm_tokens=LlmTokenUsage(
            input=tokens_in, output=tokens_out, total=tokens_in + tokens_out, by_stage=by_stage
        ),
        query_minutes=round(by_metric.get("query_execution_ms", 0) / 60_000, 2),
        seats=seats,
    )
