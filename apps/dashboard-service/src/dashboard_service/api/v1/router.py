"""The `/api/v1` router, reached only through api-gateway."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from dashboard_service.api.v1 import artifacts, dashboards, share_links
from dashboard_service.dependencies import gateway_context

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(gateway_context)])
api_router.include_router(artifacts.router)
api_router.include_router(dashboards.router)
api_router.include_router(share_links.router)
# Public at the gateway (token-gated); still only reachable through it.
api_router.include_router(share_links.public_router)
