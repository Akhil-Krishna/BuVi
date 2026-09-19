"""Composition root: request-scoped wiring for dashboard-service."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from dashboard_service.application.services.artifacts import ArtifactService
from dashboard_service.application.services.dashboards import DashboardService
from dashboard_service.application.services.share_links import ShareLinkService, SnapshotService
from dashboard_service.core.config import SCOPE_PROXY, Settings
from dashboard_service.domain.errors import (
    AuthenticationRequiredError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
    UserNotActiveError,
)
from dashboard_service.infrastructure.db.repositories.dashboard_repository import (
    DashboardRepository,
)
from dashboard_service.infrastructure.db.session import tenant_scope
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


async def get_repository(
    request: Request, principal: CurrentPrincipal
) -> AsyncIterator[DashboardRepository]:
    async with tenant_scope(
        request.app.state.session_factory, uuid.UUID(principal.tenant_id)
    ) as db:
        yield DashboardRepository(db)
        await db.commit()


ScopedRepo = Annotated[DashboardRepository, Depends(get_repository, scope="function")]


async def load_artifact_tenant_id(
    artifact_id: uuid.UUID, repository: ScopedRepo
) -> uuid.UUID | None:
    return await repository.get_artifact_tenant_id(artifact_id)


async def load_dashboard_tenant_id(
    dashboard_id: uuid.UUID, repository: ScopedRepo
) -> uuid.UUID | None:
    return await repository.get_dashboard_tenant_id(dashboard_id)


async def load_tile_tenant_id(tile_id: uuid.UUID, repository: ScopedRepo) -> uuid.UUID | None:
    return await repository.get_tile_tenant_id(tile_id)


def build_artifact_service(request: Request, repository: DashboardRepository) -> ArtifactService:
    state = request.app.state
    return ArtifactService(
        repository=repository,
        visualization=state.visualization,
        results=state.results,
        export_step_up_rows=state.settings.export_step_up_rows,
    )


def build_dashboard_service(request: Request, repository: DashboardRepository) -> DashboardService:
    state = request.app.state
    return DashboardService(
        repository=repository, visualization=state.visualization, events=state.events
    )


def build_share_link_service(request: Request, repository: DashboardRepository) -> ShareLinkService:
    state = request.app.state
    return ShareLinkService(
        repository=repository,
        dashboards=build_dashboard_service(request, repository),
        audit=state.audit,
        settings=state.settings,
    )


def build_snapshot_service(request: Request) -> SnapshotService:
    state = request.app.state
    return SnapshotService(
        session_factory=state.session_factory, results=state.results, settings=state.settings
    )
