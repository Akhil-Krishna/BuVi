"""`GET /billing/quotas` (Section 23; Phase A10): the tenant's LLM token budget for today.

The ModelRouter enforces this budget before every model call; this is the read side, for
admins who need to see how close a tenant is to it. It reads the same ledger, so the number
shown is the number enforced. Usage aggregation for billing (`/billing/usage`) is Phase A11.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from redis.exceptions import RedisError

from analytics_orchestrator.domain.errors import UpstreamUnavailableError
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
