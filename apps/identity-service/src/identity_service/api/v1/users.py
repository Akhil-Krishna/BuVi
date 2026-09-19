"""Admin user management (Sections 6.7, 7.1, 7.3, 9).

Every endpoint here takes a user id in the path, so every one of them composes
two checks, never one (Section 7.2):

    require_permission("user:manage")        -- may this kind of caller do this?
    require_resource_owner(user_tenant)      -- is this resource theirs? 404 if not.

The step-up dependency from Section 7.3 is layered on top for role changes,
deletion and forced session revocation.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status

from identity_service.api.v1.schemas import (
    InvitationCreateRequest,
    InvitationResponse,
    MfaResetResponse,
    RoleChangeRequest,
    RoleChangeResponse,
    SessionsRevokedResponse,
    UserListResponse,
    UserResponse,
)
from identity_service.core.logging import request_id_var
from identity_service.dependencies import (
    build_mfa_service,
    build_user_service,
    client_ip,
    get_repository,
    publish_role_changed,
)
from identity_service.domain.errors import NotFoundError, SelfServiceForbiddenError
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)
from platform_auth import (
    Principal,
    require_permission,
    require_resource_owner,
    require_step_up,
)
from platform_auth.permissions import PERM_ROLE_MANAGE, PERM_USER_MANAGE
from platform_contracts import IdentityRoleChanged

router = APIRouter(tags=["admin-users"])

ScopedRepo = Annotated[IdentityRepository, Depends(get_repository, scope="function")]

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


async def load_user_tenant_id(
    user_id: uuid.UUID,
    repository: ScopedRepo,
) -> uuid.UUID | None:
    """The `load_resource_tenant_id` callable required by Section 7.2.

    Loads the owning tenant and nothing else -- deliberately not the row, so no
    handler can read the user through a path that skipped the ownership check.
    """
    return await repository.get_user_tenant_id(user_id)


UserManage = Annotated[Principal, Depends(require_permission(PERM_USER_MANAGE))]
RoleManage = Annotated[Principal, Depends(require_permission(PERM_ROLE_MANAGE))]
OwnsUser = Annotated[Principal, Depends(require_resource_owner(load_user_tenant_id))]
StepUp = Annotated[Principal, Depends(require_step_up)]


async def _to_user_response(repository: IdentityRepository, user: object) -> UserResponse:
    roles = await repository.get_user_role_keys(user.id)  # type: ignore[attr-defined]
    return UserResponse(
        id=user.id,  # type: ignore[attr-defined]
        tenant_id=user.tenant_id,  # type: ignore[attr-defined]
        email=user.email,  # type: ignore[attr-defined]
        display_name=user.display_name,  # type: ignore[attr-defined]
        status=user.status,  # type: ignore[attr-defined]
        mfa_enabled=user.mfa_enabled,  # type: ignore[attr-defined]
        roles=sorted(roles),
        last_login_at=user.last_login_at,  # type: ignore[attr-defined]
        created_at=user.created_at,  # type: ignore[attr-defined]
    )


@router.get("/admin/users", response_model=UserListResponse)
async def list_users(
    principal: UserManage,
    repository: ScopedRepo,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[uuid.UUID | None, Query()] = None,
) -> UserListResponse:
    """Tenant-scoped user list (Section 9). Paginated, as all list endpoints are."""
    users, next_cursor = await repository.list_users(
        uuid.UUID(principal.tenant_id), limit=limit, cursor=cursor
    )
    return UserListResponse(
        items=[await _to_user_response(repository, user) for user in users],
        next_cursor=str(next_cursor) if next_cursor else None,
    )


@router.post(
    "/admin/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    request: Request,
    payload: InvitationCreateRequest,
    principal: UserManage,
    _step_up: StepUp,
    repository: ScopedRepo,
) -> InvitationResponse:
    """Invite a user by email and role (Section 9: `user:manage`, step-up)."""
    service = build_user_service(request, repository)
    issued = await service.invite(
        tenant_id=uuid.UUID(principal.tenant_id),
        actor_user_id=uuid.UUID(principal.user_id),
        email=payload.email,
        role_key=payload.role_key,
        ip_address=client_ip(request),
    )
    # The raw token is emailed, never returned: an admin who can list
    # invitations must not thereby be able to accept one as the invitee.
    return InvitationResponse.model_validate(issued.invitation)


@router.patch("/admin/users/{user_id}/roles", response_model=RoleChangeResponse)
async def change_roles(
    request: Request,
    user_id: uuid.UUID,
    payload: RoleChangeRequest,
    principal: RoleManage,
    _owns: OwnsUser,
    _step_up: StepUp,
    repository: ScopedRepo,
    background: BackgroundTasks,
) -> RoleChangeResponse:
    """Grant or revoke roles (Section 9: `role:manage`, step-up).

    `identity.role.changed` is published as a background task, which runs after the response
    and so after the request's transaction commits: a rolled-back change is never announced.
    """
    service = build_user_service(request, repository)
    outcome = await service.change_roles(
        tenant_id=uuid.UUID(principal.tenant_id),
        actor_user_id=uuid.UUID(principal.user_id),
        target_user_id=user_id,
        grant=frozenset(payload.grant),
        revoke=frozenset(payload.revoke),
        ip_address=client_ip(request),
    )
    if outcome.changed:
        event = IdentityRoleChanged(
            tenant_id=uuid.UUID(principal.tenant_id),
            user_id=user_id,
            roles=tuple(sorted(outcome.after)),
            granted=tuple(sorted(outcome.after - outcome.before)),
            revoked=tuple(sorted(outcome.before - outcome.after)),
            changed_by=uuid.UUID(principal.user_id),
            request_id=request_id_var.get(),
        )
        background.add_task(publish_role_changed, request, event)
    return RoleChangeResponse(user_id=user_id, roles=sorted(outcome.after))


@router.post(
    "/admin/users/{user_id}/sessions/revoke",
    response_model=SessionsRevokedResponse,
)
async def revoke_user_sessions(
    request: Request,
    user_id: uuid.UUID,
    principal: UserManage,
    _owns: OwnsUser,
    _step_up: StepUp,
    repository: ScopedRepo,
) -> SessionsRevokedResponse:
    """Force-logout a user (Sections 6.9, 9: `user:manage`, step-up)."""
    service = build_user_service(request, repository)
    count = await service.force_revoke_sessions(
        tenant_id=uuid.UUID(principal.tenant_id),
        actor_user_id=uuid.UUID(principal.user_id),
        target_user_id=user_id,
        ip_address=client_ip(request),
    )
    return SessionsRevokedResponse(sessions_revoked=count)


@router.delete("/admin/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    request: Request,
    user_id: uuid.UUID,
    principal: UserManage,
    _owns: OwnsUser,
    _step_up: StepUp,
    repository: ScopedRepo,
) -> Response:
    """Deactivate a user and cascade (Section 9; cannot remove the last admin)."""
    service = build_user_service(request, repository)
    await service.delete_user(
        tenant_id=uuid.UUID(principal.tenant_id),
        actor_user_id=uuid.UUID(principal.user_id),
        target_user_id=user_id,
        ip_address=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/admin/users/{user_id}/mfa/reset", response_model=MfaResetResponse)
async def reset_user_mfa(
    request: Request,
    user_id: uuid.UUID,
    principal: UserManage,
    _owns: OwnsUser,
    _step_up: StepUp,
    repository: ScopedRepo,
) -> MfaResetResponse:
    """Revoke every factor of another user, and their sessions (Section 7.3; Phase A10).

    For a lost device: the user logs in again and enrolls a first factor. Your own factors
    are removed through `DELETE /me/mfa/{id}`, never here.
    """
    if user_id == uuid.UUID(principal.user_id):
        raise SelfServiceForbiddenError()
    user = await repository.get_user(uuid.UUID(principal.tenant_id), user_id)
    if user is None:
        raise NotFoundError()
    factors = await build_mfa_service(request, repository).reset(actor=principal, user=user)
    sessions = await build_user_service(request, repository).force_revoke_sessions(
        tenant_id=uuid.UUID(principal.tenant_id),
        actor_user_id=uuid.UUID(principal.user_id),
        target_user_id=user_id,
        ip_address=client_ip(request),
    )
    return MfaResetResponse(factors_revoked=factors, sessions_revoked=sessions)


@router.get("/admin/users/{user_id}", response_model=UserResponse)
async def read_user(
    user_id: uuid.UUID,
    principal: UserManage,
    _owns: OwnsUser,
    repository: ScopedRepo,
) -> UserResponse:
    """Read one user. Cross-tenant ids are indistinguishable from missing ones."""
    user = await repository.get_user(uuid.UUID(principal.tenant_id), user_id)
    if user is None:
        raise NotFoundError()
    return await _to_user_response(repository, user)


@router.get("/admin/invitations", response_model=list[InvitationResponse])
async def list_invitations(
    principal: UserManage,
    repository: ScopedRepo,
) -> list[InvitationResponse]:
    invitations = await repository.list_invitations(uuid.UUID(principal.tenant_id))
    return [InvitationResponse.model_validate(row) for row in invitations]
