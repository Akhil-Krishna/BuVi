"""Composition root: request-scoped wiring for identity-service.

Two things happen here that matter for security:

* **`resolve_principal`** authenticates a caller from either the session cookie
  or an API key, and is the resolver `platform_auth.get_principal` calls. It is
  the only place a caller's authority is established.
* **`get_repository`** opens a database session already bound to the caller's
  tenant, so Section 19's RLS policies are active for the whole request. The
  unauthenticated login path uses `get_unscoped_repository`, which is
  explicitly named so an unscoped session is never obtained by accident.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from identity_service.application.services.api_key_service import ApiKeyService
from identity_service.application.services.audit_service import AuditService
from identity_service.application.services.auth_service import AuthService
from identity_service.application.services.mfa_service import MfaService
from identity_service.application.services.session_service import SessionService
from identity_service.application.services.user_service import UserService
from identity_service.core.config import Settings
from identity_service.domain.errors import AuthenticationRequiredError
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)
from identity_service.infrastructure.db.session import pre_auth_scope, tenant_scope
from identity_service.infrastructure.email.sender import EmailSender
from identity_service.infrastructure.oidc.client import OidcClient
from identity_service.infrastructure.secrets.store import SecretStore
from platform_auth import Principal

BEARER_PREFIX = "Bearer "


# --- Application-scoped singletons, installed by `main.create_app` -----------


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


def get_secret_store(request: Request) -> SecretStore:
    store: SecretStore = request.app.state.secrets
    return store


def get_oidc_client(request: Request) -> OidcClient:
    client: OidcClient = request.app.state.oidc
    return client


def get_email_sender(request: Request) -> EmailSender:
    sender: EmailSender = request.app.state.email
    return sender


def get_http_client(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient = request.app.state.http
    return client


# --- Authentication -----------------------------------------------------------


async def resolve_principal(request: Request) -> Principal:
    """Authenticate the caller (Sections 6.1, 6.8).

    Session cookie first, then `Authorization: Bearer sk_live_...`. Anything
    else is unauthenticated -- there is no third path, and in particular no
    "trusted header" that an upstream proxy could be tricked into setting.
    """
    cached = getattr(request.state, "principal", None)
    if isinstance(cached, Principal):
        return cached

    settings = get_app_settings(request)
    factory = get_session_factory(request)

    cookie_value = request.cookies.get(settings.session_cookie_name)
    if cookie_value:
        try:
            session_id = uuid.UUID(cookie_value)
        except ValueError as exc:
            raise AuthenticationRequiredError() from exc
        async with pre_auth_scope(factory) as db:
            repository = IdentityRepository(db)
            tenant_id = await repository.get_session_tenant_id(session_id)
        if tenant_id is None:
            raise AuthenticationRequiredError()
        async with tenant_scope(factory, tenant_id) as db:
            repository = IdentityRepository(db)
            service = SessionService(
                repository=repository,
                secrets=get_secret_store(request),
                settings=settings,
            )
            session_row, user, principal = await service.resolve(session_id)
            await db.commit()
        request.state.principal = principal
        request.state.session_row = session_row
        request.state.user = user
        return principal

    authorization = request.headers.get("authorization", "")
    if authorization.startswith(BEARER_PREFIX):
        presented = authorization[len(BEARER_PREFIX) :].strip()
        async with pre_auth_scope(factory) as db:
            repository = IdentityRepository(db)
            service = ApiKeyService(
                repository=repository,
                audit=AuditService(repository),
                settings=settings,
            )
            principal = await service.authenticate(presented)
            await db.commit()
        if principal is None:
            raise AuthenticationRequiredError()
        request.state.principal = principal
        return principal

    raise AuthenticationRequiredError()


CurrentPrincipal = Annotated[Principal, Depends(resolve_principal)]


# --- Request-scoped database sessions -----------------------------------------


async def get_pre_auth_repository(
    request: Request,
) -> AsyncIterator[IdentityRepository]:
    """A repository for the paths that run before a tenant is known.

    The OIDC callback and invitation acceptance both have to resolve a tenant
    from a credential before they can scope anything to it. `pre_auth_scope`
    enables the SELECT-only RLS policies that make those three lookups possible
    and nothing else; see the first migration and ADR 0002.
    """
    factory = get_session_factory(request)
    async with pre_auth_scope(factory) as session:
        yield IdentityRepository(session)
        await session.commit()


async def get_repository(
    request: Request,
    principal: CurrentPrincipal,
) -> AsyncIterator[IdentityRepository]:
    """A repository bound to the authenticated caller's tenant.

    Depending on `resolve_principal` is what guarantees the RLS scope is set
    from an authenticated identity rather than from anything caller-supplied.
    """
    factory = get_session_factory(request)
    async with tenant_scope(factory, uuid.UUID(principal.tenant_id)) as session:
        yield IdentityRepository(session)
        await session.commit()


# --- Service factories ---------------------------------------------------------


def build_audit_service(repository: IdentityRepository) -> AuditService:
    return AuditService(repository)


def build_session_service(request: Request, repository: IdentityRepository) -> SessionService:
    return SessionService(
        repository=repository,
        secrets=get_secret_store(request),
        settings=get_app_settings(request),
    )


def build_auth_service(request: Request, repository: IdentityRepository) -> AuthService:
    return AuthService(
        repository=repository,
        oidc=get_oidc_client(request),
        sessions=build_session_service(request, repository),
        audit=build_audit_service(repository),
        settings=get_app_settings(request),
    )


def build_user_service(request: Request, repository: IdentityRepository) -> UserService:
    return UserService(
        repository=repository,
        sessions=build_session_service(request, repository),
        audit=build_audit_service(repository),
        email=get_email_sender(request),
        settings=get_app_settings(request),
    )


def build_mfa_service(request: Request, repository: IdentityRepository) -> MfaService:
    return MfaService(
        repository=repository,
        secrets=get_secret_store(request),
        audit=build_audit_service(repository),
        settings=get_app_settings(request),
    )


def build_api_key_service(request: Request, repository: IdentityRepository) -> ApiKeyService:
    return ApiKeyService(
        repository=repository,
        audit=build_audit_service(repository),
        settings=get_app_settings(request),
    )


def client_ip(request: Request) -> str | None:
    """The caller's IP for audit rows.

    Reads only the socket peer. `X-Forwarded-For` is caller-controlled and is
    deliberately not trusted here; api-gateway becomes the single place that
    normalises it in Phase A2.
    """
    return request.client.host if request.client else None
