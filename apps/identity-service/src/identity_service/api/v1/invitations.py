"""Invitation acceptance (Section 6.7).

Section 9's catalog has `POST /admin/invitations` but no acceptance endpoint,
while Phase A1's Definition of Done requires the scripted flow to invite, verify
by email, and log in. This is the missing half; see ADR 0002.

The endpoint is public and token-gated, exactly like `GET /share/{token}` in
Section 9: the emailed token is the credential, so requiring a session here
would make an invitation impossible to accept.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from identity_service.api.v1.schemas import InvitationAcceptRequest, UserResponse
from identity_service.dependencies import (
    build_user_service,
    client_ip,
    get_pre_auth_repository,
)
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)

router = APIRouter(tags=["invitations"])

PreAuthRepo = Annotated[IdentityRepository, Depends(get_pre_auth_repository, scope="function")]


@router.post(
    "/auth/invitations/accept",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def accept_invitation(
    request: Request,
    payload: InvitationAcceptRequest,
    repository: PreAuthRepo,
) -> UserResponse:
    """**Public, token-gated.** Redeem an invitation and create the user.

    Unknown, expired, revoked and already-used tokens all return the same
    `INVITATION_INVALID` error, so the response cannot be used to discover which
    invitations exist.
    """
    service = build_user_service(request, repository)
    user = await service.accept_invitation(
        token=payload.token,
        idp_subject=payload.idp_subject,
        display_name=payload.display_name,
        ip_address=client_ip(request),
    )
    roles = await repository.get_user_role_keys(user.id)
    return UserResponse(
        id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        mfa_enabled=user.mfa_enabled,
        roles=sorted(roles),
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )
