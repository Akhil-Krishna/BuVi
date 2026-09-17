"""Composition root: request-scoped wiring for query-gateway."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, Request

from platform_auth import (
    CredentialRejectedError,
    IdentityTimeoutError,
    IntrospectionClient,
    IntrospectionError,
    Principal,
    PrincipalNotActiveError,
    request_credentials,
)
from query_gateway.application.services.query_service import QueryLimits, QueryService
from query_gateway.core.config import Settings
from query_gateway.domain.errors import (
    AuthenticationRequiredError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
    UserNotActiveError,
)
from query_gateway.infrastructure.db.repositories.query_repository import QueryRepository
from query_gateway.infrastructure.db.session import tenant_scope


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
    identity: IntrospectionClient = request.app.state.identity
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


@asynccontextmanager
async def repository_for(request: Request, principal: Principal) -> AsyncIterator[QueryRepository]:
    """A repository bound to `principal`'s tenant for RLS, however it was authenticated."""
    async with tenant_scope(
        request.app.state.session_factory, uuid.UUID(principal.tenant_id)
    ) as db:
        yield QueryRepository(db)
        await db.commit()


def build_query_service(request: Request, repository: QueryRepository) -> QueryService:
    state = request.app.state
    settings = get_app_settings(request)
    return QueryService(
        repository=repository,
        policies=state.policies,
        secrets=state.secrets,
        executors=state.executors,
        results=state.results,
        limiter=state.limiter,
        validator=state.validator,
        limits=QueryLimits(
            default_max_rows=settings.default_max_rows,
            max_rows_limit=settings.max_rows_limit,
            default_timeout_ms=settings.default_timeout_ms,
            max_timeout_ms=settings.max_timeout_ms,
            max_result_bytes=settings.max_result_bytes,
        ),
        purpose_callers=settings.purpose_callers,
        vault_mount=settings.vault_mount,
    )
