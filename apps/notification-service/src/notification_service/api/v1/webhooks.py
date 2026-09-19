"""`/admin/webhooks` (Section 9; Phase A11): `org_admin`; create and disable need a fresh step-up.

The subscription id in the DELETE path is checked against the caller's tenant (Section 7.2):
another tenant's id is `404`, like an unknown one.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from notification_service.api.v1.schemas import (
    WebhookCreatedResponse,
    WebhookCreateRequest,
    WebhookListResponse,
    WebhookResponse,
)
from notification_service.dependencies import (
    ScopedRepo,
    build_webhooks,
    load_subscription_tenant_id,
    require_org_admin,
)
from notification_service.infrastructure.db.models import WebhookSubscription
from platform_auth import Principal, require_resource_owner, require_step_up

router = APIRouter(tags=["webhooks"])

OrgAdmin = Annotated[Principal, Depends(require_org_admin)]
StepUp = Annotated[Principal, Depends(require_step_up)]
OwnsSubscription = Annotated[
    Principal, Depends(require_resource_owner(load_subscription_tenant_id))
]


def _view(subscription: WebhookSubscription) -> WebhookResponse:
    return WebhookResponse(
        id=subscription.id,
        url=subscription.url,
        event_types=list(subscription.event_types),
        status=subscription.status,
        created_by=subscription.created_by,
        created_at=subscription.created_at,
    )


@router.post(
    "/admin/webhooks", response_model=WebhookCreatedResponse, status_code=status.HTTP_201_CREATED
)
async def create_webhook(
    request: Request,
    response: Response,
    payload: WebhookCreateRequest,
    principal: OrgAdmin,
    _step_up: StepUp,
    repository: ScopedRepo,
) -> WebhookCreatedResponse:
    created = await build_webhooks(request, repository).create(
        principal, url=payload.url, event_types=payload.event_types
    )
    # The signing secret is in this body and nowhere else (Section 9.3: must not be stored).
    response.headers["Cache-Control"] = "no-store"
    return WebhookCreatedResponse(
        **_view(created.subscription).model_dump(), signing_secret=created.signing_secret
    )


@router.get("/admin/webhooks", response_model=WebhookListResponse)
async def list_webhooks(
    request: Request, principal: OrgAdmin, repository: ScopedRepo
) -> WebhookListResponse:
    items = await build_webhooks(request, repository).list(principal)
    return WebhookListResponse(items=[_view(s) for s in items])


@router.delete(
    "/admin/webhooks/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def disable_webhook(
    request: Request,
    subscription_id: uuid.UUID,
    principal: OrgAdmin,
    _owns: OwnsSubscription,
    _step_up: StepUp,
    repository: ScopedRepo,
) -> Response:
    await build_webhooks(request, repository).disable(principal, subscription_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
