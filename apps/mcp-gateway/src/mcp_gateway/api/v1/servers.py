"""MCP endpoints (Section 9). Every path-addressed route composes its authorization with the
resource-tenant check (Section 7.2): another tenant's server id is a 404."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from mcp_gateway.api.v1.schemas import (
    GrantCreateRequest,
    GrantResponse,
    InvokeRequest,
    InvokeResponse,
    ServerCreateRequest,
    ServerDetailResponse,
    ServerListResponse,
    ServerResponse,
    TextContent,
    ToolResponse,
)
from mcp_gateway.application.services.registry import NewServer, ServerDetail
from mcp_gateway.dependencies import (
    CurrentPrincipal,
    ScopedRepo,
    build_invocations,
    build_registry,
    load_server_tenant_id,
    require_org_admin,
)
from mcp_gateway.domain.errors import InvalidCursorError
from mcp_gateway.domain.policies.tool_policy import TOOL_NAME
from mcp_gateway.infrastructure.db.models import Server, ToolGrant
from platform_auth import Principal, require_permission, require_resource_owner, require_step_up
from platform_auth.permissions import PERM_MCP_MANAGE

router = APIRouter(tags=["mcp"])

McpManage = Annotated[Principal, Depends(require_permission(PERM_MCP_MANAGE))]
OrgAdmin = Annotated[Principal, Depends(require_org_admin)]
StepUp = Annotated[Principal, Depends(require_step_up)]
OwnsServer = Annotated[Principal, Depends(require_resource_owner(load_server_tenant_id))]
ToolName = Annotated[str, Path(pattern=TOOL_NAME.pattern, max_length=128)]
Limit = Annotated[int, Query(ge=1, le=200)]
Cursor = Annotated[str | None, Query(max_length=64)]


def _server(server: Server) -> ServerResponse:
    return ServerResponse(
        id=server.id,
        name=server.name,
        endpoint_url=server.endpoint_url,
        status=server.status,  # type: ignore[arg-type]
        has_auth_token=server.auth_secret_ref is not None,
        created_by=server.created_by,
        approved_by=server.approved_by,
        created_at=server.created_at,
    )


def _grant(grant: ToolGrant) -> GrantResponse:
    return GrantResponse.model_validate(grant, from_attributes=True)


def _detail(detail: ServerDetail) -> ServerDetailResponse:
    return ServerDetailResponse(
        **_server(detail.server).model_dump(),
        tools=[
            ToolResponse(
                id=tool.id,
                name=tool.tool_name,
                tool_class=tool.tool_class,  # type: ignore[arg-type]
                default_policy=tool.default_policy,  # type: ignore[arg-type]
                grants=[_grant(g) for g in detail.grants if g.tool_id == tool.id],
            )
            for tool in detail.tools
        ],
    )


@router.get("/mcp/servers", response_model=ServerListResponse)
async def list_servers(
    request: Request,
    _principal: McpManage,
    repository: ScopedRepo,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> ServerListResponse:
    try:
        after = uuid.UUID(cursor) if cursor else None
    except ValueError:
        raise InvalidCursorError() from None
    rows, next_key = await build_registry(request, repository).list_servers(
        limit=limit, after=after
    )
    return ServerListResponse(
        items=[_server(s) for s in rows], next_cursor=str(next_key) if next_key else None
    )


@router.post(
    "/mcp/servers", response_model=ServerDetailResponse, status_code=status.HTTP_201_CREATED
)
async def register_server(
    request: Request, payload: ServerCreateRequest, principal: McpManage, repository: ScopedRepo
) -> ServerDetailResponse:
    """`pending_approval` with a declared tool manifest; the server is not contacted."""
    detail = await build_registry(request, repository).register(
        principal,
        NewServer(
            name=payload.name,
            endpoint_url=payload.endpoint_url,
            auth_token=payload.auth_token.get_secret_value() if payload.auth_token else None,
            tools=[(t.name, t.tool_class) for t in payload.tools],
        ),
    )
    return _detail(detail)


@router.get("/mcp/servers/{server_id}", response_model=ServerDetailResponse)
async def get_server(
    request: Request,
    server_id: uuid.UUID,
    _principal: McpManage,
    _owns: OwnsServer,
    repository: ScopedRepo,
) -> ServerDetailResponse:
    return _detail(await build_registry(request, repository).detail(server_id))


@router.post("/mcp/servers/{server_id}/approve", response_model=ServerDetailResponse)
async def approve_server(
    request: Request,
    server_id: uuid.UUID,
    principal: OrgAdmin,
    _step_up: StepUp,
    _owns: OwnsServer,
    repository: ScopedRepo,
) -> ServerDetailResponse:
    """Discovers the live tools and verifies the declared manifest before approving."""
    return _detail(await build_registry(request, repository).approve(principal, server_id))


@router.post("/mcp/servers/{server_id}/disable", response_model=ServerDetailResponse)
async def disable_server(
    request: Request,
    server_id: uuid.UUID,
    principal: OrgAdmin,
    _owns: OwnsServer,
    repository: ScopedRepo,
) -> ServerDetailResponse:
    """Stop every invocation now. No step-up: removing access never waits on MFA."""
    return _detail(await build_registry(request, repository).disable(principal, server_id))


@router.post("/mcp/servers/{server_id}/reject", response_model=ServerDetailResponse)
async def reject_server(
    request: Request,
    server_id: uuid.UUID,
    principal: OrgAdmin,
    _owns: OwnsServer,
    repository: ScopedRepo,
) -> ServerDetailResponse:
    return _detail(await build_registry(request, repository).reject(principal, server_id))


@router.post(
    "/mcp/servers/{server_id}/tools/{tool}/grants",
    response_model=GrantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def grant_tool(
    request: Request,
    server_id: uuid.UUID,
    tool: ToolName,
    payload: GrantCreateRequest,
    principal: OrgAdmin,
    _owns: OwnsServer,
    repository: ScopedRepo,
) -> GrantResponse:
    grant = await build_registry(request, repository).grant(
        principal,
        server_id,
        tool,
        grantee_role=payload.grantee_role,
        grantee_user_id=payload.grantee_user_id,
    )
    return _grant(grant)


@router.delete(
    "/mcp/servers/{server_id}/tools/{tool}/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def revoke_grant(
    request: Request,
    server_id: uuid.UUID,
    tool: ToolName,
    grant_id: uuid.UUID,
    principal: OrgAdmin,
    _owns: OwnsServer,
    repository: ScopedRepo,
) -> Response:
    await build_registry(request, repository).revoke(principal, server_id, tool, grant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/mcp/servers/{server_id}/tools/{tool}/invoke", response_model=InvokeResponse)
async def invoke_tool(
    request: Request,
    server_id: uuid.UUID,
    tool: ToolName,
    payload: InvokeRequest,
    principal: CurrentPrincipal,
    _owns: OwnsServer,
    repository: ScopedRepo,
) -> InvokeResponse:
    """Needs a grant, not a coarse permission (Section 9). Output is untrusted (Section 14)."""
    outcome = await build_invocations(request, repository).invoke(
        principal, server_id, tool, payload.arguments
    )
    result = outcome.result
    return InvokeResponse(
        invocation_id=outcome.invocation_id,
        is_error=result.is_error,
        content=[TextContent(type="text", text=block["text"]) for block in result.content],
        structured_content=result.structured_content,
        dropped_blocks=result.dropped_blocks,
    )
