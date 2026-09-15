"""`POST /internal/v1/queries` (Section 13). Service-to-service only; no public route in Phase A4.

Two credentials are required, and neither substitutes for the other:

* the calling service's JWT with `query-gateway:execute` (Section 6.3), and
* the end user's forwarded session or API key, re-authenticated here -- a user is never taken
  from a header or a body field.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from platform_auth import ServiceIdentity, require_service_scope
from query_gateway.application.services.query_service import QueryCommand
from query_gateway.core.config import SCOPE_EXECUTE
from query_gateway.dependencies import CurrentPrincipal, ScopedRepo, build_query_service
from query_gateway.domain.value_objects.policy import Purpose

router = APIRouter(prefix="/internal/v1", tags=["internal"])


class QueryRequest(BaseModel):
    """Section 13's request body."""

    model_config = ConfigDict(extra="forbid")

    database_id: uuid.UUID
    sql: Annotated[str, Field(min_length=1, max_length=100_000)]
    purpose: Literal["analytics_run", "sql_editor", "export"]
    max_rows: Annotated[int, Field(ge=1)] | None = None
    timeout_ms: Annotated[int, Field(ge=100)] | None = None
    run_id: uuid.UUID | None = None


class QueryColumnResponse(BaseModel):
    name: str
    type: str


class QueryResponse(BaseModel):
    query_id: uuid.UUID
    status: Literal["succeeded"]
    columns: list[QueryColumnResponse]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    truncation_reason: Literal["rows", "bytes"] | None
    bytes_returned: int
    duration_ms: int
    tables: list[str]
    result_handle: str
    result_expires_at: dt.datetime


@router.post("/queries", response_model=QueryResponse)
async def run_query(
    request: Request,
    payload: QueryRequest,
    service: Annotated[ServiceIdentity, Depends(require_service_scope(SCOPE_EXECUTE))],
    principal: CurrentPrincipal,
    repository: ScopedRepo,
) -> QueryResponse:
    outcome = await build_query_service(request, repository).execute(
        principal,
        service.subject,
        QueryCommand(
            data_source_id=payload.database_id,
            sql=payload.sql,
            purpose=Purpose(payload.purpose),
            max_rows=payload.max_rows,
            timeout_ms=payload.timeout_ms,
            run_id=payload.run_id,
        ),
    )
    result = outcome.result
    return QueryResponse(
        query_id=outcome.query_id,
        status="succeeded",
        columns=[QueryColumnResponse(name=c.name, type=c.type) for c in result.columns],
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        truncation_reason=result.truncation_reason,  # type: ignore[arg-type]
        bytes_returned=result.bytes_returned,
        duration_ms=outcome.duration_ms,
        tables=list(outcome.tables),
        result_handle=outcome.stored.handle,
        result_expires_at=outcome.stored.expires_at,
    )
