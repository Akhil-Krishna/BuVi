"""Metadata wire contracts (Section 18.1)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field

from platform_contracts.analytics import _Versioned


class MetadataSyncCompleted(_Versioned):
    """`metadata.sync.completed`: metadata-service (while sync runs in-request) ->
    notification-service. `user_id` ran the sync and is the one notified."""

    tenant_id: uuid.UUID
    data_source_id: uuid.UUID
    data_source_name: str = Field(max_length=200)
    status: Literal["succeeded", "failed"]
    tables_synced: int = Field(ge=0)
    user_id: uuid.UUID
    request_id: str | None = Field(default=None, max_length=128)
