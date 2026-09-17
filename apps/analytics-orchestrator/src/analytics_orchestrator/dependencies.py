"""Composition root: request-scoped wiring for analytics-orchestrator."""

from __future__ import annotations

import ipaddress
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from analytics_orchestrator.application.services.conversation_service import ConversationService
from analytics_orchestrator.core.config import SCOPE_PROXY, Settings
from analytics_orchestrator.domain.errors import (
    AuthenticationRequiredError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
    UserNotActiveError,
)
from analytics_orchestrator.infrastructure.db.repositories.analytics_repository import (
    AnalyticsRepository,
)
from analytics_orchestrator.infrastructure.db.session import tenant_scope
from platform_auth import (
    CredentialRejectedError,
    IdentityTimeoutError,
    IntrospectionClient,
    IntrospectionError,
    Principal,
    PrincipalNotActiveError,
    request_credentials,
    verify_service_request,
)


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


CurrentPrincipal = Annotated[Principal, Depends(resolve_principal)]


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
) -> AsyncIterator[AnalyticsRepository]:
    async with tenant_scope(
        request.app.state.session_factory, uuid.UUID(principal.tenant_id)
    ) as db:
        yield AnalyticsRepository(db)
        await db.commit()


ScopedRepo = Annotated[AnalyticsRepository, Depends(get_repository, scope="function")]


async def load_conversation_tenant_id(
    conversation_id: uuid.UUID, repository: ScopedRepo
) -> uuid.UUID | None:
    return await repository.get_conversation_tenant_id(conversation_id)


async def load_run_tenant_id(run_id: uuid.UUID, repository: ScopedRepo) -> uuid.UUID | None:
    return await repository.get_run_tenant_id(run_id)


def build_conversation_service(
    request: Request, repository: AnalyticsRepository
) -> ConversationService:
    state = request.app.state
    return ConversationService(repository=repository, queue=state.queue, events=state.events)
