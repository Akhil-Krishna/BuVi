"""Request/response schemas (Sections 9, 9.1, 16). Responses never carry `validated_sql`, the
result handle, or another user's private data."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_TEXT = r"^[^<>{}`\x00-\x1f]*$"


class HealthResponse(BaseModel):
    status: str
    service: str
    checks: dict[str, str] = Field(default_factory=dict)


# --- artifacts ---------------------------------------------------------------------------------
class SourceRefResponse(BaseModel):
    data_source_id: uuid.UUID
    tables: list[str]


class ArtifactResponse(BaseModel):
    """Section 9.1 `GET /artifacts/{id}`."""

    artifact_id: uuid.UUID
    title: str
    summary: str
    chart_spec: dict[str, Any]
    result_schema: list[dict[str, Any]]
    source_refs: list[SourceRefResponse]
    refresh_policy: dict[str, Any]
    version: int
    can_pin: bool
    conversation_id: uuid.UUID | None
    run_id: uuid.UUID | None
    created_at: dt.datetime


class ResultColumnResponse(BaseModel):
    name: str
    type: str


class ArtifactDataResponse(BaseModel):
    artifact_id: uuid.UUID
    columns: list[ResultColumnResponse]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    expires_at: dt.datetime


class SourceRefIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_source_id: uuid.UUID
    tables: Annotated[list[Annotated[str, Field(max_length=200)]], Field(max_length=50)]


class ArtifactCreateRequest(BaseModel):
    """Internal: a run's artifact (analytics-orchestrator `persist_artifact`)."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: uuid.UUID
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    run_id: uuid.UUID
    title: Annotated[str, Field(min_length=1, max_length=200)]
    summary: Annotated[str, Field(max_length=500)] = ""
    semantic_query: dict[str, Any]
    source_refs: Annotated[list[SourceRefIn], Field(max_length=20)]
    validated_sql: Annotated[str, Field(min_length=1, max_length=100_000)]
    query_result_ref: Annotated[str, Field(min_length=1, max_length=512)]
    result_schema: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=200)]
    chart_spec: dict[str, Any]
    created_by: uuid.UUID


class ArtifactStoredResponse(BaseModel):
    artifact_id: uuid.UUID
    version: int
    created: bool


# --- dashboards --------------------------------------------------------------------------------
class DashboardCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=120, pattern=_TEXT)]
    #: `link` is reached through share links only (Phase A10).
    visibility: Literal["private", "tenant"] = "private"


class DashboardResponse(BaseModel):
    id: uuid.UUID
    name: str
    visibility: str
    owner_id: uuid.UUID
    is_owner: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class DashboardListResponse(BaseModel):
    items: list[DashboardResponse]
    next_cursor: str | None


class TilePosition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    x: int
    y: int
    w: int
    h: int


class TileResponse(BaseModel):
    """Section 16 `DashboardTile`."""

    id: uuid.UUID
    dashboard_id: uuid.UUID
    artifact_id: uuid.UUID
    chart_spec_version: int
    position: TilePosition
    overrides: dict[str, Any]
    created_at: dt.datetime


class DashboardDetailResponse(DashboardResponse):
    tiles: list[TileResponse]


class PinRequest(BaseModel):
    """Section 9: body `{artifact_id}`."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: uuid.UUID


class TileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: TilePosition | None = None
    #: Section 17 `options` keys only; validated by visualization-service against the artifact.
    overrides: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _something(self) -> TileUpdateRequest:
        if self.position is None and self.overrides is None:
            raise ValueError("position or overrides is required")
        return self


# --- Share links and the guest snapshot (Phase A10) -------------------------------------------


class ShareLinkCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Capped by `share_link_max_hours`; the default is `share_link_default_hours`.
    expires_in_hours: int | None = Field(default=None, ge=1, le=720)


class ShareLinkResponse(BaseModel):
    """Never the token: it is shown once, at creation."""

    id: uuid.UUID
    created_by: uuid.UUID
    created_at: dt.datetime
    expires_at: dt.datetime
    revoked_at: dt.datetime | None
    active: bool


class ShareLinkCreatedResponse(ShareLinkResponse):
    token: str
    url: str


class ShareLinkListResponse(BaseModel):
    items: list[ShareLinkResponse]


class SnapshotTileResponse(BaseModel):
    title: str
    position: dict[str, Any]
    chart_spec: dict[str, Any]
    overrides: dict[str, Any]
    data: dict[str, Any] | None
    data_status: Literal["ok", "expired", "too_large"]


class SnapshotResponse(BaseModel):
    """A read-only dashboard for a link holder: charts and their data, nothing else."""

    name: str
    expires_at: dt.datetime
    tiles: list[SnapshotTileResponse]
