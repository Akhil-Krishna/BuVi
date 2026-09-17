"""`ChartSpec` -- the only channel from an agent to the renderer (Section 17).

Strict by construction: every model forbids unknown keys (a smuggled key is rejected, not
silently dropped), accepts exactly one spelling per key (the wire alias), does no type coercion,
and text fields refuse markup characters. This module is the DTO and its exported JSON Schema
only; checking a spec against an artifact's result schema is visualization-service's validator
(Section 17, Phase A6).
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

FieldType = Literal["temporal", "quantitative", "nominal", "ordinal"]
ChartType = Literal["line", "bar", "area", "scatter", "pie", "table"]

_FIELD_PATTERN: Final = r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"
#: Plain text only: no markup, template, or control characters.
_TEXT_PATTERN: Final = r"^[^<>{}`\x00-\x1f]*$"

#: One spelling per key in both directions: parse and dump by alias only, so a persisted spec
#: round-trips and the wire never sees `color_scheme` next to `colorScheme`.
_STRICT = ConfigDict(
    extra="forbid",
    frozen=True,
    strict=True,
    validate_by_name=False,
    validate_by_alias=True,
    serialize_by_alias=True,
)


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
