"""Request/response models (Section 9)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from platform_egress import MAX_URL_LENGTH


class HealthResponse(BaseModel):
    status: str
    service: str
    checks: dict[str, str] = Field(default_factory=dict)


class NotificationResponse(BaseModel):
    id: uuid.UUID
    template_key: str
    title: str
    body: str
    payload: dict[str, Any]
    status: str
    created_at: dt.datetime
    read_at: dt.datetime | None


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    unread: int
    next_cursor: str | None


class WebhookCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=MAX_URL_LENGTH)
    event_types: list[str] = Field(min_length=1, max_length=10)


class WebhookResponse(BaseModel):
    id: uuid.UUID
    url: str
    event_types: list[str]
    status: str
    created_by: uuid.UUID
    created_at: dt.datetime


class WebhookCreatedResponse(WebhookResponse):
    #: Shown once; sign-verification key for `X-Buvi-Signature`.
    signing_secret: str


class WebhookListResponse(BaseModel):
    items: list[WebhookResponse]
