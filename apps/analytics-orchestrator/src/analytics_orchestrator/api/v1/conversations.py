"""Chat endpoints (Section 9): `chat:use` plus the resource-tenant check on ids (Section 7.2)."""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status

from analytics_orchestrator.api.v1.schemas import (
    ConversationCreateRequest,
    ConversationResponse,
    MessageCreateRequest,
    RunAcceptedResponse,
    RunStatusResponse,
)
from analytics_orchestrator.dependencies import (
    ScopedRepo,
    build_conversation_service,
    load_conversation_tenant_id,
    load_run_tenant_id,
)
from analytics_orchestrator.domain.errors import ValidationFailedError
from platform_auth import Principal, require_permission, require_resource_owner
from platform_auth.permissions import PERM_CHAT_USE

router = APIRouter(tags=["chat"])

ChatUse = Annotated[Principal, Depends(require_permission(PERM_CHAT_USE))]
OwnsConversation = Annotated[
    Principal, Depends(require_resource_owner(load_conversation_tenant_id))
]
OwnsRun = Annotated[Principal, Depends(require_resource_owner(load_run_tenant_id))]
_IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{1,255}$")


@router.post(
    "/conversations", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED
)
async def create_conversation(
    request: Request, payload: ConversationCreateRequest, principal: ChatUse, repository: ScopedRepo
) -> ConversationResponse:
    conversation = await build_conversation_service(request, repository).create_conversation(
        principal, payload.title
    )
    return ConversationResponse(
        id=conversation.id, title=conversation.title, created_at=conversation.created_at
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=RunAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_message(
    request: Request,
    response: Response,
    conversation_id: uuid.UUID,
    payload: MessageCreateRequest,
    principal: ChatUse,
    _owns: OwnsConversation,
    repository: ScopedRepo,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RunAcceptedResponse:
    """Section 9.1: returns `{run_id}`; the run executes asynchronously (Section 18)."""
    if idempotency_key is not None and not _IDEMPOTENCY_KEY.match(idempotency_key):
        raise ValidationFailedError(field="Idempotency-Key")
    accepted = await build_conversation_service(request, repository).post_message(
        principal,
        conversation_id,
        content=payload.content,
        data_source_id=payload.data_source_id,
        idempotency_key=idempotency_key,
    )
    if accepted.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return RunAcceptedResponse(run_id=accepted.run.id, conversation_id=accepted.run.conversation_id)


@router.post(
    "/runs/{run_id}/cancel", response_model=RunStatusResponse, status_code=status.HTTP_202_ACCEPTED
)
async def cancel_run(
    request: Request, run_id: uuid.UUID, principal: ChatUse, _owns: OwnsRun, repository: ScopedRepo
) -> RunStatusResponse:
    """Best-effort (Section 9): a queued run ends now; a running one stops before its next step."""
    run = await build_conversation_service(request, repository).cancel(principal, run_id)
    return RunStatusResponse(run_id=run.id, status=run.status, error_code=run.error_code)
