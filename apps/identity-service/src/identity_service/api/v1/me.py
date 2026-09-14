"""Self-service endpoints: sessions and API keys (Sections 6.8, 6.9, 9).

`GET /me/sessions` and `DELETE /me/sessions/{id}` are not in the Section 9
catalog, but Section 6.9 requires that "users can view and revoke their own
active sessions" and Phase A1 lists "sessions list/revoke" as deliverable. They
are added under the existing `/me` prefix Section 9 already uses for API keys;
see ADR 0002.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from identity_service.api.v1.schemas import (
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyListResponse,
    ApiKeyResponse,
    UserSessionListResponse,
    UserSessionResponse,
)
from identity_service.dependencies import (
    CurrentPrincipal,
    build_api_key_service,
    build_session_service,
    client_ip,
    get_repository,
)
from identity_service.domain.errors import AuthenticationRequiredError, NotFoundError
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)

router = APIRouter(tags=["me"])

ScopedRepo = Annotated[IdentityRepository, Depends(get_repository, scope="function")]


# --- Sessions (Section 6.9) ---------------------------------------------------


@router.get("/me/sessions", response_model=UserSessionListResponse)
async def list_my_sessions(
    principal: CurrentPrincipal,
    repository: ScopedRepo,
) -> UserSessionListResponse:
    """The caller's own active sessions (Section 6.9)."""
    rows = await repository.list_sessions_for_user(
        uuid.UUID(principal.tenant_id), uuid.UUID(principal.user_id)
    )
    current_id = principal.session_id
    return UserSessionListResponse(
        items=[
            UserSessionResponse(
                id=row.id,
                device_label=row.device_label,
                ip_address=str(row.ip_address) if row.ip_address else None,
                user_agent=row.user_agent,
                created_at=row.created_at,
                last_seen_at=row.last_seen_at,
                expires_at=row.expires_at,
                current=str(row.id) == current_id,
            )
            for row in rows
        ]
    )


@router.delete("/me/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_my_session(
    request: Request,
    session_id: uuid.UUID,
    principal: CurrentPrincipal,
    repository: ScopedRepo,
) -> Response:
    """Revoke one of the caller's own sessions.

    Ownership is checked against `principal.user_id`, not only against the
    tenant: a same-tenant colleague's session id must not be revocable here.
    A session belonging to anyone else answers 404, like any other resource the
    caller may not address (Section 7.2).
    """
    row = await repository.get_session(session_id)
    if row is None or str(row.user_id) != principal.user_id:
        raise NotFoundError()

    service = build_session_service(request, repository)
    if not await service.revoke(uuid.UUID(principal.tenant_id), session_id):
        raise NotFoundError()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- API keys (Sections 6.8, 9) -----------------------------------------------


@router.get("/me/api-keys", response_model=ApiKeyListResponse)
async def list_my_api_keys(
    principal: CurrentPrincipal,
    repository: ScopedRepo,
) -> ApiKeyListResponse:
    rows = await repository.list_api_keys_for_owner(
        uuid.UUID(principal.tenant_id), uuid.UUID(principal.user_id)
    )
    return ApiKeyListResponse(items=[ApiKeyResponse.model_validate(row) for row in rows])


@router.post(
    "/me/api-keys",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_my_api_key(
    request: Request,
    payload: ApiKeyCreateRequest,
    principal: CurrentPrincipal,
    repository: ScopedRepo,
) -> ApiKeyCreatedResponse:
    """Mint an API key. The secret appears in this response and nowhere else.

    An API key cannot mint another API key: allowing it would let a leaked key
    with a short expiry bootstrap itself into a permanent one.
    """
    if principal.auth_method != "session":
        raise AuthenticationRequiredError("API keys can only be created from a user session.")

    service = build_api_key_service(request, repository)
    issued = await service.create(
        tenant_id=uuid.UUID(principal.tenant_id),
        actor_user_id=uuid.UUID(principal.user_id),
        name=payload.name,
        scopes=frozenset(payload.scopes),
        expires_at=payload.expires_at,
        ip_address=client_ip(request),
    )
    return ApiKeyCreatedResponse(
        api_key=ApiKeyResponse.model_validate(issued.api_key),
        secret=issued.secret,
    )


@router.delete("/me/api-keys/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_my_api_key(
    request: Request,
    api_key_id: uuid.UUID,
    principal: CurrentPrincipal,
    repository: ScopedRepo,
) -> Response:
    """Revoke a key the caller owns, or any key in the tenant with `user:manage`.

    Section 9: "session (owner) or `user:manage`".
    """
    row = await repository.get_api_key(uuid.UUID(principal.tenant_id), api_key_id)
    if row is None:
        raise NotFoundError()

    is_owner = str(row.owner_user_id) == principal.user_id
    if not is_owner and not principal.has_permission("user:manage"):
        # Not theirs and no admin permission: indistinguishable from missing.
        raise NotFoundError()

    service = build_api_key_service(request, repository)
    await service.revoke(
        tenant_id=uuid.UUID(principal.tenant_id),
        actor_user_id=uuid.UUID(principal.user_id),
        api_key_id=api_key_id,
        ip_address=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
