"""Request/response models. Requests forbid unknown fields; responses carry ids and statuses only
-- never prompts, model output, SQL, or result rows (Sections 9.1, 11)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    service: str
    checks: dict[str, str] = Field(default_factory=dict)


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Annotated[str, Field(max_length=200)] | None = None


class ConversationResponse(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: dt.datetime


class MessageCreateRequest(BaseModel):
    """Section 9.1."""

    model_config = ConfigDict(extra="forbid")
    content: Annotated[str, Field(min_length=1, max_length=4_000)]
    data_source_id: uuid.UUID | None = None


class RunAcceptedResponse(BaseModel):
    run_id: uuid.UUID
    conversation_id: uuid.UUID


class RunStatusResponse(BaseModel):
    run_id: uuid.UUID
    status: str
    error_code: str | None = None


class RunEventsResponse(BaseModel):
    run_id: uuid.UUID
    status: str
    #: `AnalyticsRunEvent` wire objects (Section 11), in `seq` order.
    events: list[dict[str, Any]]
