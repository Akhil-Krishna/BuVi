"""Composition root: request-scoped wiring for notification-service."""

from __future__ import annotations

import ipaddress
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from notification_service.application.services.webhooks import WebhookService
from notification_service.core.config import SCOPE_PROXY, Settings
from notification_service.domain.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
    UserNotActiveError,
)
from notification_service.infrastructure.db.repositories.notification_repository import (
    NotificationRepository,
)
from notification_service.infrastructure.db.session import tenant_scope
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
    """Section 9: webhooks are `org_admin` actions (checked again behind the gateway)."""
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
) -> AsyncIterator[NotificationRepository]:
    async with tenant_scope(
        request.app.state.session_factory, uuid.UUID(principal.tenant_id)
    ) as db:
        yield NotificationRepository(db)
        await db.commit()


ScopedRepo = Annotated[NotificationRepository, Depends(get_repository, scope="function")]


async def load_subscription_tenant_id(
    subscription_id: uuid.UUID, repository: ScopedRepo
) -> uuid.UUID | None:
    return await repository.get_subscription_tenant_id(subscription_id)


def build_webhooks(request: Request, repository: NotificationRepository) -> WebhookService:
    state = request.app.state
    return WebhookService(
        repository=repository,
        secrets=state.secrets,
        egress=state.egress,
        audit=state.audit,
        max_per_tenant=state.settings.max_webhooks_per_tenant,
        client_ip=getattr(request.state, "forwarded_client_ip", None),
    )
