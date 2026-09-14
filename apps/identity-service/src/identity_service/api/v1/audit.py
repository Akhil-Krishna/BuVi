"""Audit read API (Sections 8.1, 22, 9).

Section 9 lists `GET /admin/audit` under `audit:read`. The write side lives in
`AuditService`; there is deliberately no write endpoint, because the log is
append-only and populated as a side effect of the operations being audited.

The full admin audit surface (filtering by actor and date range) is Phase A10;
this is the tenant-scoped read the Phase A1 Definition of Done needs in order to
assert that login, logout and role changes actually produce audit rows.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from identity_service.api.v1.schemas import AuditEventListResponse, AuditEventResponse
from identity_service.dependencies import get_repository
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)
from platform_auth import Principal, require_permission
from platform_auth.permissions import PERM_AUDIT_READ

router = APIRouter(tags=["admin-audit"])

ScopedRepo = Annotated[IdentityRepository, Depends(get_repository, scope="function")]
AuditRead = Annotated[Principal, Depends(require_permission(PERM_AUDIT_READ))]

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


@router.get("/admin/audit", response_model=AuditEventListResponse)
async def list_audit_events(
    principal: AuditRead,
    repository: ScopedRepo,
    event_type: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> AuditEventListResponse:
    """Tenant-scoped audit events, newest first."""
    rows = await repository.list_audit_events(
        uuid.UUID(principal.tenant_id), limit=limit, event_type=event_type
    )
    return AuditEventListResponse(items=[AuditEventResponse.model_validate(row) for row in rows])
