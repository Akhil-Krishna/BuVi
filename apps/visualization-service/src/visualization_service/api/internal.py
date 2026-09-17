"""`POST /internal/v1/chart-specs/validate` (Section 17). Service-to-service only: callers are
analytics-orchestrator (the Flow) and dashboard-service (before storing an artifact or accepting
tile overrides), each with a token carrying `visualization-service:validate`.

The body's `chart_spec` and `overrides` are untyped JSON on purpose: parsing them into a model at
the HTTP layer would reject or drop unknown keys before the validator could report them.
An invalid spec is a *result* (`200`, `valid: false`), not a request error.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from platform_auth import ServiceIdentity, require_service_scope
from visualization_service.application.services.chart_validation import check_chart_spec
from visualization_service.core.config import SCOPE_VALIDATE

router = APIRouter(prefix="/internal/v1", tags=["internal"])


class ValidateChartSpecRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart_spec: Any
    result_schema: Annotated[list[dict[str, Any]], Field(max_length=200)]
    overrides: dict[str, Any] | None = None


class ValidateChartSpecResponse(BaseModel):
    valid: bool
    chart_spec: dict[str, Any] | None = None
    problems: list[str] = Field(default_factory=list)


@router.post("/chart-specs/validate", response_model=ValidateChartSpecResponse)
async def validate(
    payload: ValidateChartSpecRequest,
    _service: Annotated[ServiceIdentity, Depends(require_service_scope(SCOPE_VALIDATE))],
) -> ValidateChartSpecResponse:
    outcome = check_chart_spec(payload.chart_spec, payload.result_schema, payload.overrides)
    return ValidateChartSpecResponse(
        valid=outcome.valid, chart_spec=outcome.chart_spec, problems=outcome.problems
    )
