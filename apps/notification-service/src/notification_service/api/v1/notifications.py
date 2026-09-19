"""`/me/notifications` (Section 9; Phase A11): the caller's own in-app notifications."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from notification_service.api.v1.schemas import NotificationListResponse, NotificationResponse
from notification_service.application.services.inbox import InboxService
from notification_service.dependencies import CurrentPrincipal, ScopedRepo

router = APIRouter(tags=["notifications"])


@router.get("/me/notifications", response_model=NotificationListResponse)
async def list_notifications(
    principal: CurrentPrincipal,
    repository: ScopedRepo,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> NotificationListResponse:
    page = await InboxService(repository).page(
        principal, unread_only=unread_only, limit=limit, cursor=cursor
    )
    return NotificationListResponse(
        items=[
            NotificationResponse(
                id=item.notification.id,
                template_key=item.notification.template_key,
                title=item.text.title,
                body=item.text.body,
                payload=item.notification.payload,
                status=item.notification.status,
                created_at=item.notification.created_at,
                read_at=item.notification.read_at,
            )
            for item in page.items
        ],
        unread=page.unread,
        next_cursor=page.next_cursor,
    )


@router.post(
    "/me/notifications/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def mark_read(
    notification_id: uuid.UUID, principal: CurrentPrincipal, repository: ScopedRepo
) -> Response:
    await InboxService(repository).mark_read(principal, notification_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
