"""The `/api/v1` router, reached only through api-gateway."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from mcp_gateway.api.v1 import servers
from mcp_gateway.dependencies import gateway_context

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(gateway_context)])
api_router.include_router(servers.router)
