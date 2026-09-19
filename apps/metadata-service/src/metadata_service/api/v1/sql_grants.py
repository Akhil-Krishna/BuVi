"""Per-connection `sql:execute` grants (Section 7.1; Phase A10), managed by `org_admin`.

A developer holds `sql:execute` from their role, but runs SQL-editor queries only on the data
sources they are granted here; query-gateway checks the grant on every call, uncached, so a
revocation takes effect on the next query. `org_admin` needs no grant.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict

from metadata_service.application.services.data_source_service import RESOURCE_TYPE
from metadata_service.dependencies import (
    CurrentPrincipal,
    ScopedRepo,
    client_ip,
    get_audit_sink,
    load_data_source_tenant_id,
)
from metadata_service.domain.errors import ForbiddenError, NotFoundError, SqlGrantExistsError
from metadata_service.infrastructure.audit.sink import AuditRecord
from metadata_service.infrastructure.db.models import DataSourceGrant
from platform_auth import Principal, require_resource_owner
from platform_auth.permissions import ROLE_ORG_ADMIN

router = APIRouter(tags=["sql-grants"])


def require_org_admin(principal: CurrentPrincipal) -> Principal:
    """Section 9: grants are an `org_admin` decision (the gateway checks it first)."""
    if ROLE_ORG_ADMIN not in principal.roles:
        raise ForbiddenError()
    return principal


OrgAdmin = Annotated[Principal, Depends(require_org_admin)]
OwnsDataSource = Annotated[Principal, Depends(require_resource_owner(load_data_source_tenant_id))]


class SqlGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID


class SqlGrantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    granted_by: uuid.UUID
    granted_at: dt.datetime


class SqlGrantListResponse(BaseModel):
    items: list[SqlGrantResponse]


async def _record(
    request: Request, principal: Principal, event_type: str, grant: DataSourceGrant
) -> None:
    await get_audit_sink(request).record(
        AuditRecord(
            tenant_id=grant.tenant_id,
            actor_user_id=uuid.UUID(principal.user_id),
            event_type=event_type,
            resource_type=RESOURCE_TYPE,
            resource_id=str(grant.data_source_id),
            after_state={"user_id": str(grant.user_id), "grant_id": str(grant.id)},
            ip_address=client_ip(request),
        )
    )


@router.get("/data-sources/{data_source_id}/sql-grants", response_model=SqlGrantListResponse)
async def list_sql_grants(
    data_source_id: uuid.UUID,
    principal: OrgAdmin,
    _owns: OwnsDataSource,
    repository: ScopedRepo,
) -> SqlGrantListResponse:
    grants = await repository.list_sql_grants(uuid.UUID(principal.tenant_id), data_source_id)
    return SqlGrantListResponse(items=[SqlGrantResponse.model_validate(g) for g in grants])


@router.post(
    "/data-sources/{data_source_id}/sql-grants",
    response_model=SqlGrantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_sql_grant(
    request: Request,
    data_source_id: uuid.UUID,
    payload: SqlGrantRequest,
    principal: OrgAdmin,
    _owns: OwnsDataSource,
    repository: ScopedRepo,
) -> SqlGrantResponse:
    grant = await repository.add_sql_grant(
        DataSourceGrant(
            tenant_id=uuid.UUID(principal.tenant_id),
            data_source_id=data_source_id,
            user_id=payload.user_id,
            granted_by=uuid.UUID(principal.user_id),
        )
    )
    if grant is None:
        raise SqlGrantExistsError()
    await repository.commit()
    await _record(request, principal, "connection.sql_grant_added", grant)
    return SqlGrantResponse.model_validate(grant)


@router.delete(
    "/data-sources/{data_source_id}/sql-grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def revoke_sql_grant(
    request: Request,
    data_source_id: uuid.UUID,
    grant_id: uuid.UUID,
    principal: OrgAdmin,
    _owns: OwnsDataSource,
    repository: ScopedRepo,
) -> Response:
    grant = await repository.delete_sql_grant(
        uuid.UUID(principal.tenant_id), data_source_id, grant_id
    )
    if grant is None:
        raise NotFoundError()
    await repository.commit()
    await _record(request, principal, "connection.sql_grant_revoked", grant)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
