"""The `/api/v1` router, reached only through api-gateway."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from notification_service.api.v1 import notifications, webhooks
from notification_service.dependencies import gateway_context

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(gateway_context)])
api_router.include_router(notifications.router)
api_router.include_router(webhooks.router)
