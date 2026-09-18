"""Composition root: request-scoped wiring for mcp-gateway."""

from __future__ import annotations

import ipaddress
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from mcp_gateway.application.services.invocation import InvocationService
from mcp_gateway.application.services.registry import ServerRegistry
from mcp_gateway.core.config import SCOPE_PROXY, Settings
from mcp_gateway.domain.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
    UserNotActiveError,
)
from mcp_gateway.infrastructure.db.repositories.mcp_repository import McpRepository
from mcp_gateway.infrastructure.db.session import tenant_scope
from platform_auth import (
    CredentialRejectedError,
    IdentityTimeoutError,
    IntrospectionClient,
    IntrospectionError,
    Principal,
    PrincipalNotActiveError,
    get_principal,
    request_credentials,
    verify_service_request,
)
from platform_auth.permissions import ROLE_ORG_ADMIN


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def resolve_principal(request: Request) -> Principal:
    """Authenticate the forwarded user credential through identity-service (Section 6.3)."""
    cached = getattr(request.state, "principal", None)
    if isinstance(cached, Principal):
        return cached
    session_token, api_key = request_credentials(
        request, get_app_settings(request).session_cookie_name
    )
    if not session_token and not api_key:
        raise AuthenticationRequiredError()
    identity: IntrospectionClient = request.app.state.introspection
    try:
        principal = await identity.introspect(session_token=session_token, api_key=api_key)
    except CredentialRejectedError:
        raise AuthenticationRequiredError() from None
    except PrincipalNotActiveError:
        raise UserNotActiveError() from None
    except IdentityTimeoutError:
        raise UpstreamTimeoutError() from None
    except IntrospectionError:
        raise UpstreamUnavailableError() from None
    request.state.principal = principal
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_org_admin(principal: CurrentPrincipal) -> Principal:
    """Section 9: approval and grants are `org_admin` actions (checked again behind the gateway)."""
    if ROLE_ORG_ADMIN not in principal.roles:
        raise ForbiddenError()
    return principal


async def gateway_context(request: Request) -> None:
    identity = await verify_service_request(request)
    if identity is None:
        if get_app_settings(request).require_gateway_token:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail="Service authentication required"
            )
        return
    if SCOPE_PROXY not in identity.scopes:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Insufficient service scope")
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
    try:
        request.state.forwarded_client_ip = str(ipaddress.ip_address(forwarded))
    except ValueError:
        request.state.forwarded_client_ip = None


async def get_repository(
    request: Request, principal: CurrentPrincipal
) -> AsyncIterator[McpRepository]:
    async with tenant_scope(
        request.app.state.session_factory, uuid.UUID(principal.tenant_id)
    ) as db:
        yield McpRepository(db)
        await db.commit()


ScopedRepo = Annotated[McpRepository, Depends(get_repository, scope="function")]


async def load_server_tenant_id(server_id: uuid.UUID, repository: ScopedRepo) -> uuid.UUID | None:
    return await repository.get_server_tenant_id(server_id)


def build_registry(request: Request, repository: McpRepository) -> ServerRegistry:
    state = request.app.state
    return ServerRegistry(
        repository=repository,
        servers=state.mcp,
        secrets=state.secrets,
        egress=state.egress,
        audit=state.audit,
        client_ip=getattr(request.state, "forwarded_client_ip", None),
    )


def build_invocations(request: Request, repository: McpRepository) -> InvocationService:
    state = request.app.state
    return InvocationService(
        repository=repository,
        servers=state.mcp,
        secrets=state.secrets,
        egress=state.egress,
        audit=state.audit,
        events=state.events,
        max_argument_bytes=state.settings.max_argument_bytes,
        client_ip=getattr(request.state, "forwarded_client_ip", None),
    )
