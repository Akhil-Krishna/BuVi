"""`POST /internal/v1/queries` and `POST /internal/v1/queries/validate` (Section 13).

Service-to-service only. Two credentials are required and neither substitutes for the other:

* the calling service's JWT with `query-gateway:execute` (Section 6.3), and
* the end user: a forwarded session or API key re-authenticated here -- or, for
  analytics-orchestrator's queued runs only, `on_behalf_of`, re-resolved from identity-service.
  Permissions are never taken from a header or a body field.

`POST /internal/v1/results/read` returns the stored rows behind an `analytics_run` result handle
for dashboard-service (`query-gateway:results`); the user was authorized there (`artifact:read`
plus the artifact's tenant), so no user credential is sent here.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from platform_auth import Principal, ServiceIdentity, request_credentials, require_service_scope
from query_gateway.application.services.query_service import QueryCommand
from query_gateway.application.services.result_reader import ResultReader
from query_gateway.core.config import SCOPE_EXECUTE, SCOPE_RESULTS
from query_gateway.dependencies import (
    build_query_service,
    get_app_settings,
    repository_for,
    resolve_principal,
)
from query_gateway.domain.errors import (
    DelegationNotAllowedError,
    ResultReaderNotAllowedError,
    ValidationFailedError,
)
from query_gateway.domain.value_objects.policy import Purpose
from query_gateway.infrastructure.db.repositories.query_repository import QueryRepository
from query_gateway.infrastructure.db.session import tenant_scope

router = APIRouter(prefix="/internal/v1", tags=["internal"])

ExecuteScope = Annotated[ServiceIdentity, Depends(require_service_scope(SCOPE_EXECUTE))]
ResultsScope = Annotated[ServiceIdentity, Depends(require_service_scope(SCOPE_RESULTS))]
PurposeName = Literal["analytics_run", "sql_editor", "export"]


class OnBehalfOf(BaseModel):
    """The user a queued analytics run acts for; re-resolved from identity-service."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID
    user_id: uuid.UUID


class ValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database_id: uuid.UUID
    sql: Annotated[str, Field(min_length=1, max_length=100_000)]
    purpose: PurposeName
    run_id: uuid.UUID | None = None
    on_behalf_of: OnBehalfOf | None = None


class QueryRequest(ValidateRequest):
    """Section 13's request body."""

    max_rows: Annotated[int, Field(ge=1)] | None = None
    timeout_ms: Annotated[int, Field(ge=100)] | None = None


class ValidateResponse(BaseModel):
    query_id: uuid.UUID
    valid: Literal[True]
    #: The SQL that would execute: regenerated from the validated tree.
    sql: str
    tables: list[str]
    normalized_sql_sha256: str


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


async def _principal_for(
    request: Request, service: ServiceIdentity, body: ValidateRequest
) -> Principal:
    if body.on_behalf_of is None:
        return await resolve_principal(request)
    settings = get_app_settings(request)
    if (
        service.subject not in settings.delegating_callers
        or body.purpose != "analytics_run"
        or body.run_id is None
    ):
        raise DelegationNotAllowedError()
    if any(request_credentials(request, settings.session_cookie_name)):
        raise ValidationFailedError(reason="send either a user credential or on_behalf_of")
    principal: Principal = await request.app.state.identity_resolver.resolve(
        body.on_behalf_of.tenant_id, body.on_behalf_of.user_id
    )
    request.state.principal = principal
    return principal


def _command(body: ValidateRequest) -> QueryCommand:
    return QueryCommand(
        data_source_id=body.database_id,
        sql=body.sql,
        purpose=Purpose(body.purpose),
        max_rows=body.max_rows if isinstance(body, QueryRequest) else None,
        timeout_ms=body.timeout_ms if isinstance(body, QueryRequest) else None,
        run_id=body.run_id,
    )


@router.post("/queries/validate", response_model=ValidateResponse)
async def validate_query(
    request: Request, payload: ValidateRequest, service: ExecuteScope
) -> ValidateResponse:
    """Authorization and validation without execution; audited `validated` or `rejected`."""
    principal = await _principal_for(request, service, payload)
    async with repository_for(request, principal) as repository:
        validated = await build_query_service(request, repository).validate(
            principal, service.subject, _command(payload)
        )
    return ValidateResponse(
        query_id=validated.query_id,
        valid=True,
        sql=validated.sql,
        tables=list(validated.tables),
        normalized_sql_sha256=validated.sql_hash,
    )


@router.post("/queries", response_model=QueryResponse)
async def run_query(
    request: Request, payload: QueryRequest, service: ExecuteScope
) -> QueryResponse:
    principal = await _principal_for(request, service, payload)
    async with repository_for(request, principal) as repository:
        outcome = await build_query_service(request, repository).execute(
            principal, service.subject, _command(payload)
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


class ResultReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID
    result_handle: Annotated[str, Field(min_length=1, max_length=512)]


class ResultReadResponse(BaseModel):
    query_id: uuid.UUID
    columns: list[QueryColumnResponse]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    expires_at: dt.datetime


@router.post("/results/read", response_model=ResultReadResponse)
async def read_result(
    request: Request, payload: ResultReadRequest, service: ResultsScope
) -> ResultReadResponse:
    settings = get_app_settings(request)
    if service.subject not in settings.result_readers:
        raise ResultReaderNotAllowedError()
    async with tenant_scope(request.app.state.session_factory, payload.tenant_id) as db:
        stored = await ResultReader(
            repository=QueryRepository(db),
            results=request.app.state.results,
            ttl_days=settings.result_ttl_days,
        ).read(payload.tenant_id, payload.result_handle)
        await db.commit()
    return ResultReadResponse(
        query_id=stored.query_id,
        columns=[QueryColumnResponse(**c) for c in stored.columns],
        rows=stored.rows,
        row_count=len(stored.rows),
        truncated=stored.truncated,
        expires_at=stored.expires_at,
    )
