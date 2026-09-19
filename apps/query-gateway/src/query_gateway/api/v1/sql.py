"""The public SQL API (Section 9: `/sql/validate`, `/sql/execute`, `/sql/history`; Phase A10).

Reached only through api-gateway (its service token with `query-gateway:proxy`), with the
user's own session forwarded and re-authenticated here. Purpose `sql_editor`: the same
validator, executor, limits and audit as the chat flow, never a second execution path
(Section 13). On top of `sql:execute`:
* a per-connection grant, for everyone but `org_admin` (Section 7.1);
* a fresh step-up above the export row threshold (Section 7.3);
* the tenant's concurrency cap (`429 QUERY_CONCURRENCY_LIMITED`).

Responses carry rows, never the stored result handle.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from platform_auth import Principal, ServiceIdentity, require_service_scope
from platform_auth.permissions import PERM_RUN_DEBUG, PERM_SQL_EXECUTE
from query_gateway.application.services.query_service import QueryCommand
from query_gateway.core.config import SCOPE_PROXY
from query_gateway.dependencies import build_query_service, repository_for, resolve_principal
from query_gateway.domain.errors import ForbiddenError, InvalidCursorError
from query_gateway.domain.value_objects.policy import Purpose

router = APIRouter(prefix="/api/v1", tags=["sql"])

Gateway = Annotated[ServiceIdentity, Depends(require_service_scope(SCOPE_PROXY))]
User = Annotated[Principal, Depends(resolve_principal)]


class SqlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database_id: uuid.UUID
    sql: Annotated[str, Field(min_length=1, max_length=100_000)]


class SqlExecuteRequest(SqlRequest):
    max_rows: Annotated[int, Field(ge=1)] | None = None
    timeout_ms: Annotated[int, Field(ge=100)] | None = None


class SqlValidateResponse(BaseModel):
    query_id: uuid.UUID
    valid: Literal[True]
    sql: str
    tables: list[str]


class SqlColumn(BaseModel):
    name: str
    type: str


class SqlExecuteResponse(BaseModel):
    query_id: uuid.UUID
    columns: list[SqlColumn]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    truncation_reason: Literal["rows", "bytes"] | None
    duration_ms: int
    tables: list[str]


class SqlHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    data_source_id: uuid.UUID
    requested_by: str
    status: str
    sql_text: str
    row_count: int | None
    duration_ms: int | None
    error_code: str | None
    created_at: dt.datetime


class SqlHistoryResponse(BaseModel):
    items: list[SqlHistoryItem]
    next_cursor: str | None


def _command(body: SqlRequest) -> QueryCommand:
    return QueryCommand(
        data_source_id=body.database_id,
        sql=body.sql,
        purpose=Purpose.SQL_EDITOR,
        max_rows=body.max_rows if isinstance(body, SqlExecuteRequest) else None,
        timeout_ms=body.timeout_ms if isinstance(body, SqlExecuteRequest) else None,
        run_id=None,
    )


@router.post("/sql/validate", response_model=SqlValidateResponse)
async def validate_sql(
    request: Request, payload: SqlRequest, service: Gateway, principal: User
) -> SqlValidateResponse:
    """Dry validation: the SQL that would run, or `422 QUERY_VALIDATION_FAILED`."""
    async with repository_for(request, principal) as repository:
        validated = await build_query_service(request, repository).validate(
            principal, service.subject, _command(payload)
        )
    return SqlValidateResponse(
        query_id=validated.query_id, valid=True, sql=validated.sql, tables=list(validated.tables)
    )


@router.post("/sql/execute", response_model=SqlExecuteResponse)
async def execute_sql(
    request: Request, payload: SqlExecuteRequest, service: Gateway, principal: User
) -> SqlExecuteResponse:
    async with repository_for(request, principal) as repository:
        outcome = await build_query_service(request, repository).execute(
            principal, service.subject, _command(payload)
        )
    result = outcome.result
    return SqlExecuteResponse(
        query_id=outcome.query_id,
        columns=[SqlColumn(name=c.name, type=c.type) for c in result.columns],
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        truncation_reason=result.truncation_reason,  # type: ignore[arg-type]
        duration_ms=outcome.duration_ms,
        tables=list(outcome.tables),
    )


@router.get("/sql/history", response_model=SqlHistoryResponse)
async def sql_history(
    request: Request,
    _service: Gateway,
    principal: User,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
) -> SqlHistoryResponse:
    """The caller's own SQL-editor queries; the tenant's with `run:debug` (Section 9)."""
    if principal.has_permission(PERM_RUN_DEBUG):
        requested_by = None
    elif principal.has_permission(PERM_SQL_EXECUTE):
        requested_by = principal.user_id
    else:
        raise ForbiddenError()
    try:
        before = uuid.UUID(cursor) if cursor else None
    except ValueError:
        raise InvalidCursorError() from None
    async with repository_for(request, principal) as repository:
        rows = await repository.history(
            uuid.UUID(principal.tenant_id),
            purpose=Purpose.SQL_EDITOR.value,
            requested_by=requested_by,
            limit=limit + 1,
            before=before,
        )
    page = rows[:limit]
    return SqlHistoryResponse(
        items=[SqlHistoryItem.model_validate(r) for r in page],
        next_cursor=str(page[-1].id) if len(rows) > limit else None,
    )
