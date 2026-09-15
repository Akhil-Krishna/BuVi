"""The `/api/v1` router. Health probes are mounted outside the versioned prefix."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from metadata_service.api.v1 import catalog, data_sources
from metadata_service.dependencies import gateway_context

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(gateway_context)])
api_router.include_router(data_sources.router)
api_router.include_router(catalog.router)
