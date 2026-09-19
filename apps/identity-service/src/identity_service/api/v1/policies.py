"""Roles catalog and tenant policies (Sections 2, 3, 6.6, 7.1; Phase A10).

Roles are the fixed Section 2 tenant roles: this service lists them, it never creates them.
A policy change takes effect on each caller's next request, because every principal is
rebuilt from the policies at introspection.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, replace
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from identity_service.api.v1.schemas import PoliciesPatchRequest, PoliciesResponse, RoleResponse
from identity_service.application.services import audit_service as events
from identity_service.dependencies import build_audit_service, client_ip, get_repository
from identity_service.domain.errors import WebAuthnNotEnrolledError
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)
from platform_auth import Principal, require_permission, require_step_up
from platform_auth.permissions import (
    PERM_POLICY_MANAGE,
    PERM_ROLE_MANAGE,
    TENANT_ROLES,
    permissions_for_roles,
)

router = APIRouter(tags=["admin-policies"])

ScopedRepo = Annotated[IdentityRepository, Depends(get_repository, scope="function")]
RoleManage = Annotated[Principal, Depends(require_permission(PERM_ROLE_MANAGE))]
PolicyManage = Annotated[Principal, Depends(require_permission(PERM_POLICY_MANAGE))]
StepUp = Annotated[Principal, Depends(require_step_up)]


@router.get("/admin/roles", response_model=list[RoleResponse])
async def list_roles(_principal: RoleManage) -> list[RoleResponse]:
    """The tenant roles and the Section 7.1 permissions each carries, before tenant policy."""
    return [
        RoleResponse(key=key, permissions=sorted(permissions_for_roles(frozenset({key}))))
        for key in sorted(TENANT_ROLES)
    ]


@router.get("/admin/policies", response_model=PoliciesResponse)
async def read_policies(principal: PolicyManage, repository: ScopedRepo) -> PoliciesResponse:
    policies = await repository.get_policies(uuid.UUID(principal.tenant_id))
    return PoliciesResponse(**asdict(policies))


@router.patch("/admin/policies", response_model=PoliciesResponse)
async def change_policies(
    request: Request,
    payload: PoliciesPatchRequest,
    principal: PolicyManage,
    _step_up: StepUp,
    repository: ScopedRepo,
) -> PoliciesResponse:
    """Change tenant policies (step-up, audited with before and after)."""
    tenant_id = uuid.UUID(principal.tenant_id)
    before = await repository.get_policies(tenant_id)
    after = replace(before, **payload.model_dump(exclude_none=True))
    if after.org_admin_requires_webauthn and not before.org_admin_requires_webauthn:
        # Turning it on must not lock the acting admin out of every step-up operation
        # (including turning it off again): they need a WebAuthn key first.
        keys = await repository.list_mfa_credentials(
            tenant_id, uuid.UUID(principal.user_id), method="webauthn", confirmed_only=True
        )
        if not keys:
            raise WebAuthnNotEnrolledError()
    if after != before:
        await repository.save_policies(tenant_id, after, updated_by=uuid.UUID(principal.user_id))
        await build_audit_service(repository, request).record(
            event_type=events.EVENT_POLICIES_CHANGED,
            tenant_id=tenant_id,
            actor_user_id=uuid.UUID(principal.user_id),
            resource_type="tenant",
            resource_id=str(tenant_id),
            before_state=asdict(before),
            after_state=asdict(after),
            ip_address=client_ip(request),
        )
    return PoliciesResponse(**asdict(after))
