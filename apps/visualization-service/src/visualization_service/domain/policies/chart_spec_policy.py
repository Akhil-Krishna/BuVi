"""The ChartSpec validator (Section 17) -- a pure function, no I/O.

`validate_chart_spec` takes the *raw* JSON object an agent (or a stored artifact, or a tile
override) produced, never a pre-parsed model, so an unknown key is seen and rejected rather than
dropped by an earlier parse. It returns the normalized spec or raises `ChartSpecInvalidError`
whose `problems` name locations and rules only -- never the offending input, which may be an
injection attempt.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from pydantic import ValidationError

from platform_contracts import ChartOptions, ChartSpec, Encoding, ResultField

MAX_PROBLEMS: Final = 20
_NEEDS_XY: Final = frozenset({"line", "bar", "area", "scatter", "pie"})
_QUANTITATIVE_Y: Final = frozenset({"line", "bar", "area", "pie"})
_SAFE_KEY: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")


class ChartSpecInvalidError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems[:MAX_PROBLEMS]


def _schema_problems(error: ValidationError, prefix: str = "") -> list[str]:
    problems = []
    for item in error.errors(include_input=False, include_url=False, include_context=False):
        # A location can hold an attacker-chosen key name (an unknown key): name it only if plain.
        location = (
            ".".join(
                str(part) if isinstance(part, int) or _SAFE_KEY.match(str(part)) else "<key>"
                for part in item["loc"]
            )
            or "$"
        )
        problems.append(f"{prefix}{location}: {item['type']}")
    return problems


def _result_fields(result_schema: Sequence[Mapping[str, Any] | ResultField]) -> dict[str, str]:
    fields: dict[str, str] = {}
    problems: list[str] = []
    for index, raw in enumerate(result_schema):
        try:
            field = raw if isinstance(raw, ResultField) else ResultField.model_validate(raw)
        except ValidationError as error:
            problems += _schema_problems(error, f"result_schema.{index}.")
            continue
        fields[field.field] = field.type
    if problems:
        raise ChartSpecInvalidError(problems)
    return fields


def _encoding_problems(spec: ChartSpec, fields: Mapping[str, str]) -> list[str]:
    problems: list[str] = []
    for channel in ("x", "y", "color"):
        encoding: Encoding | None = getattr(spec.encoding, channel)
        if encoding is None:
            continue
        actual = fields.get(encoding.field)
        if actual is None:
            problems.append(f"encoding.{channel}.field: not in result_schema")
        elif actual != encoding.type and not (
            encoding.type in ("nominal", "ordinal") and actual != "quantitative"
        ):
            problems.append(f"encoding.{channel}.type: field is {actual}, not {encoding.type}")
    if spec.type in _NEEDS_XY and (spec.encoding.x is None or spec.encoding.y is None):
        problems.append(f"encoding: {spec.type} requires x and y")
    if (
        spec.type in _QUANTITATIVE_Y
        and spec.encoding.y is not None
        and spec.encoding.y.type != "quantitative"
    ):
        problems.append(f"encoding.y.type: {spec.type} requires quantitative")
    if spec.encoding.color is not None and spec.encoding.color.type == "quantitative":
        problems.append("encoding.color.type: must be nominal, ordinal or temporal")
    return problems


def validate_chart_spec(
    raw_spec: Any,
    result_schema: Sequence[Mapping[str, Any] | ResultField],
    overrides: Any = None,
) -> ChartSpec:
    """Validate `raw_spec` against Section 17 and the artifact's `result_schema`.

    `overrides` (a dashboard tile's) may only carry `options` keys; they are applied on top of the
    spec's options and the merged spec is validated as a whole.
    """
    fields = _result_fields(result_schema)
    if not isinstance(raw_spec, Mapping):
        raise ChartSpecInvalidError(["$: must be an object"])
    try:
        spec = ChartSpec.model_validate(raw_spec)
    except ValidationError as error:
        raise ChartSpecInvalidError(_schema_problems(error)) from None
    if overrides is not None:
        if not isinstance(overrides, Mapping):
            raise ChartSpecInvalidError(["overrides: must be an object"])
        try:
            ChartOptions.model_validate(overrides)
        except ValidationError as error:
            raise ChartSpecInvalidError(_schema_problems(error, "overrides.")) from None
        merged = {**spec.options.model_dump(by_alias=True, exclude_none=True), **overrides}
        try:
            spec = ChartSpec.model_validate({**spec.to_wire(), "options": merged})
        except ValidationError as error:
            raise ChartSpecInvalidError(_schema_problems(error, "overrides.")) from None
    problems = _encoding_problems(spec, fields)
    if problems:
        raise ChartSpecInvalidError(problems)
    return spec
