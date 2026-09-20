"""Invitation acceptance (Sections 6.7, 9).

`POST /invitations/{token}/accept` is public and token-gated, but the token alone
never creates an account. Accepting starts a real IdP login; the user is created
only in `/auth/callback`, from the verified ID token, and only if its email
matches the invited address (Section 6.7). No identity is taken from the request.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from identity_service.api.v1.auth import start_login_response
from identity_service.core.config import Settings
from identity_service.dependencies import get_app_settings, get_pre_auth_repository
from identity_service.domain.errors import InvitationInvalidError
from identity_service.domain.value_objects.tokens import hash_token
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)

router = APIRouter(tags=["invitations"])

PreAuthRepo = Annotated[IdentityRepository, Depends(get_pre_auth_repository, scope="function")]
AppSettings = Annotated[Settings, Depends(get_app_settings)]


@router.post("/invitations/{token}/accept", status_code=status.HTTP_303_SEE_OTHER)
async def accept_invitation(
    request: Request,
    settings: AppSettings,
    repository: PreAuthRepo,
    token: Annotated[str, Path(min_length=16, max_length=256)],
    redirect_uri: Annotated[str | None, Query(max_length=2048)] = None,
) -> Response:
    """**Public, token-gated.** Start the IdP login that will redeem this invitation.

    Unknown, expired, revoked and already-used tokens all return the same
    `INVITATION_INVALID`, so the response cannot reveal which invitations exist.
    `redirect_uri` follows the same two-value allow-list as `GET /auth/login`
    (ADR 0018).
    """
    token_hash = hash_token(token)
    invitation = await repository.find_pending_invitation_by_token_hash(token_hash)
    if invitation is None or invitation.expires_at <= dt.datetime.now(dt.UTC):
        raise InvitationInvalidError()
    return start_login_response(
        request,
        settings,
        repository,
        status_code=status.HTTP_303_SEE_OTHER,
        redirect_uri=redirect_uri,
        invitation_token_hash=token_hash,
    )
