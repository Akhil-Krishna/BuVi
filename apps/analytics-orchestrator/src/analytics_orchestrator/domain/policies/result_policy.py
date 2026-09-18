"""Result statistics and insight grounding (ADR 0009) -- pure functions.

The model never sees a row: `result_stats` reduces rows to per-column aggregates (quantitative),
ranges (temporal) and distinct counts (nominal). `insight_problems` rejects any number in an
insight that is not one of those statistics, the row count, or a number the user wrote.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final

from analytics_orchestrator.domain.value_objects.agent_outputs import ResultInsight
from analytics_orchestrator.domain.value_objects.run_state import ColumnStats, ResultStats
from platform_contracts import ResultField

_NUMBER: Final = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?![\w])")


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _round(value: Decimal) -> float:
    return float(round(value, 4))


def result_stats(
    schema: Sequence[ResultField], rows: Sequence[Sequence[Any]], *, row_count: int, truncated: bool
) -> ResultStats:
    columns: list[ColumnStats] = []
    for index, field in enumerate(schema):
        values = [row[index] for row in rows if index < len(row) and row[index] is not None]
        if field.type == "quantitative":
            numbers = [n for n in (_decimal(v) for v in values) if n is not None]
            total = sum(numbers, Decimal(0))
            columns.append(
                ColumnStats(
                    field=field.field,
                    type=field.type,
                    count=len(numbers),
                    min=_round(min(numbers)) if numbers else None,
                    max=_round(max(numbers)) if numbers else None,
                    sum=_round(total) if numbers else None,
                    mean=_round(total / len(numbers)) if numbers else None,
                )
            )
        elif field.type == "temporal":
            texts = sorted(str(v) for v in values)
            columns.append(
                ColumnStats(
                    field=field.field,
                    type=field.type,
                    count=len(texts),
                    min=texts[0][:32] if texts else None,
                    max=texts[-1][:32] if texts else None,
                )
            )
        else:
            columns.append(
                ColumnStats(
                    field=field.field,
                    type=field.type,
                    count=len(values),
                    distinct_count=len({str(v) for v in values}),
                )
            )
    return ResultStats(row_count=row_count, truncated=truncated, columns=columns)


def _numbers_in(text: str) -> list[str]:
    return [m.group(0).replace(",", "") for m in _NUMBER.finditer(text)]


def _date_parts(value: str) -> set[Decimal]:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return set()
    return {Decimal(parsed.year), Decimal(parsed.month), Decimal(parsed.day)}


def grounded_values(stats: ResultStats, request_text: str) -> set[Decimal]:
    values: set[Decimal] = {Decimal(stats.row_count)}
    for column in stats.columns:
        values.add(Decimal(column.count))
        if column.distinct_count is not None:
            values.add(Decimal(column.distinct_count))
        for value in (column.min, column.max, column.sum, column.mean):
            if isinstance(value, str):
                values |= _date_parts(value)
            elif value is not None:
                values.add(Decimal(str(value)))
    for token in _numbers_in(request_text):
        number = _decimal(token)
        if number is not None:
            values.add(number)
    return values


def _matches(written: str, allowed: set[Decimal]) -> bool:
    number = _decimal(written)
    if number is None:
        return False
    exponent = number.as_tuple().exponent
    places = max(-exponent, 0) if isinstance(exponent, int) else 0
    quantum = Decimal(1).scaleb(-places)
    # Half-up, as people round when they write a figure (Decimal defaults to banker's rounding).
    return any(value.quantize(quantum, rounding=ROUND_HALF_UP) == number for value in allowed)


def insight_problems(insight: ResultInsight, stats: ResultStats, request_text: str) -> list[str]:
    allowed = grounded_values(stats, request_text)
    ungrounded = [
        number
        for text in (insight.headline, *insight.observations)
        for number in _numbers_in(text)
        if not _matches(number, allowed)
    ]
    return [f"number {n} is not in the result statistics" for n in dict.fromkeys(ungrounded)]
