"""The `/api/v1` router, reached only through api-gateway."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from semantic_service.api.v1 import metrics
from semantic_service.dependencies import gateway_context

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(gateway_context)])
api_router.include_router(metrics.router)
