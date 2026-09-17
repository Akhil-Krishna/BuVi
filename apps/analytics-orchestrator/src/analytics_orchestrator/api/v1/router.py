"""The `/api/v1` router, reached only through api-gateway."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from analytics_orchestrator.api.v1 import conversations
from analytics_orchestrator.dependencies import gateway_context

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(gateway_context)])
api_router.include_router(conversations.router)
