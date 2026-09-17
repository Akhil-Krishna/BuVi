"""Use case: validate a ChartSpec for a caller and report the outcome as data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from visualization_service.domain.policies.chart_spec_policy import (
    ChartSpecInvalidError,
    validate_chart_spec,
)


@dataclass(frozen=True)
class ValidationOutcome:
    valid: bool
    chart_spec: dict[str, Any] | None = None
    problems: list[str] = field(default_factory=list)


def check_chart_spec(
    chart_spec: Any, result_schema: list[dict[str, Any]], overrides: Any = None
) -> ValidationOutcome:
    try:
        spec = validate_chart_spec(chart_spec, result_schema, overrides)
    except ChartSpecInvalidError as error:
        return ValidationOutcome(valid=False, problems=error.problems)
    return ValidationOutcome(valid=True, chart_spec=spec.to_wire())
