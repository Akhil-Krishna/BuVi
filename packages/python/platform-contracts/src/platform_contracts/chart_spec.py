"""`ChartSpec` -- the only channel from an agent to the renderer (Section 17).

Strict by construction: every model forbids unknown keys (a smuggled key is rejected, not
silently dropped), text fields refuse markup characters, and `validate_chart_spec` checks every
encoding against the artifact's own result schema. Phase A6's visualization-service validates
the JSON Schema generated from these models.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

FieldType = Literal["temporal", "quantitative", "nominal", "ordinal"]
ChartType = Literal["line", "bar", "area", "scatter", "pie", "table"]

_FIELD_PATTERN: Final = r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"
#: Plain text only: no markup, template, or control characters.
_TEXT_PATTERN: Final = r"^[^<>{}`\x00-\x1f]*$"

_STRICT = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ResultField(BaseModel):
    """One column of an artifact's `result_schema`."""

    model_config = _STRICT
    field: str = Field(pattern=_FIELD_PATTERN)
    type: FieldType


class Encoding(BaseModel):
    model_config = _STRICT
    field: str = Field(pattern=_FIELD_PATTERN)
    type: FieldType


class ChartEncodings(BaseModel):
    model_config = _STRICT
    x: Encoding | None = None
    y: Encoding | None = None
    color: Encoding | None = None


class ChartOptions(BaseModel):
    """Section 17: title, legend, stacking, colorScheme, axis labels -- nothing else."""

    model_config = _STRICT
    title: str | None = Field(default=None, max_length=120, pattern=_TEXT_PATTERN)
    legend: bool = True
    stacking: Literal["none", "stacked", "percent"] | None = None
    color_scheme: Literal["default", "categorical", "sequential", "diverging"] | None = Field(
        default=None, alias="colorScheme"
    )
    x_axis_label: str | None = Field(
        default=None, alias="xAxisLabel", max_length=60, pattern=_TEXT_PATTERN
    )
    y_axis_label: str | None = Field(
        default=None, alias="yAxisLabel", max_length=60, pattern=_TEXT_PATTERN
    )


class ChartSpec(BaseModel):
    model_config = _STRICT
    type: ChartType
    dataset: Literal["artifact-result"] = "artifact-result"
    encoding: ChartEncodings = Field(default_factory=ChartEncodings)
    options: ChartOptions = Field(default_factory=ChartOptions)

    def to_wire(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True)


class ChartSpecError(ValueError):
    """The spec is well-formed but incompatible with the result. `problems` are safe to log."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


_NEEDS_XY: Final = frozenset({"line", "bar", "area", "scatter", "pie"})
_QUANTITATIVE_Y: Final = frozenset({"line", "bar", "area", "pie"})


def validate_chart_spec(spec: ChartSpec, result_schema: Sequence[ResultField]) -> None:
    """Raise `ChartSpecError` unless every encoding fits the result schema and the chart type."""
    fields = {f.field: f.type for f in result_schema}
    problems: list[str] = []
    for channel in ("x", "y", "color"):
        encoding: Encoding | None = getattr(spec.encoding, channel)
        if encoding is None:
            continue
        actual = fields.get(encoding.field)
        if actual is None:
            problems.append(f"{channel}: unknown field {encoding.field}")
        elif actual != encoding.type and not (
            encoding.type in ("nominal", "ordinal") and actual != "quantitative"
        ):
            problems.append(f"{channel}: {encoding.field} is {actual}, not {encoding.type}")
    if spec.type in _NEEDS_XY and (spec.encoding.x is None or spec.encoding.y is None):
        problems.append(f"{spec.type} requires x and y encodings")
    if (
        spec.type in _QUANTITATIVE_Y
        and spec.encoding.y is not None
        and spec.encoding.y.type != "quantitative"
    ):
        problems.append(f"{spec.type} requires a quantitative y")
    if spec.encoding.color is not None and spec.encoding.color.type == "quantitative":
        problems.append("color must be nominal, ordinal or temporal")
    if problems:
        raise ChartSpecError(problems)
