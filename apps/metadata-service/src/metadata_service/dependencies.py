"""Composition root: request-scoped wiring for metadata-service.

* **`resolve_principal`** authenticates the *forwarded credential* (session cookie or
  API key) through identity-service introspection. A principal asserted by an upstream
  header is never trusted -- only credentials are forwarded (Section 6.3).
* **`gateway_context`** verifies the api-gateway service token on `/api/v1`.
* **`get_repository`** opens a session bound to the authenticated caller's tenant, so
  RLS is active for the whole request (Section 19).
"""

from __future__ import annotations

import ipaddress
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from metadata_service.application.services.catalog_sync_service import CatalogSyncService
from metadata_service.application.services.data_source_service import DataSourceService
from metadata_service.core.config import SCOPE_PROXY, Settings
from metadata_service.domain.errors import (
    AuthenticationRequiredError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
    UserNotActiveError,
)
from metadata_service.infrastructure.audit.sink import AuditSink
from metadata_service.infrastructure.connectors.base import CatalogConnector
from metadata_service.infrastructure.db.repositories.metadata_repository import (
    MetadataRepository,
)
from metadata_service.infrastructure.db.session import tenant_scope
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
from platform_egress import EgressPolicy
from platform_secrets import SecretStore

# --- Application-scoped singletons, installed by `main.create_app` ------------------


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


def get_secret_store(request: Request) -> SecretStore:
    store: SecretStore = request.app.state.secrets
    return store


def get_connectors(request: Request) -> Mapping[str, CatalogConnector]:
    connectors: Mapping[str, CatalogConnector] = request.app.state.connectors
    return connectors


def get_egress_policy(request: Request) -> EgressPolicy:
    policy: EgressPolicy = request.app.state.egress
    return policy


def get_audit_sink(request: Request) -> AuditSink:
    sink: AuditSink = request.app.state.audit
    return sink


def get_identity_client(request: Request) -> IntrospectionClient:
    client: IntrospectionClient = request.app.state.identity
    return client


# --- Authentication -------------------------------------------------------------------


async def resolve_principal(request: Request) -> Principal:
    """Authenticate the forwarded credential. The resolver behind `get_principal`."""
    cached = getattr(request.state, "principal", None)
    if isinstance(cached, Principal):
        return cached
    session_token, api_key = request_credentials(
        request, get_app_settings(request).session_cookie_name
    )
    if not session_token and not api_key:
        raise AuthenticationRequiredError()
    try:
        principal = await get_identity_client(request).introspect(
            session_token=session_token, api_key=api_key
        )
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
    """Router-level check on `/api/v1` (Section 6.3).

    A present service token must be valid and carry `metadata-service:proxy`; when
    `require_gateway_token` is on it must be present. Only a request proven to come
    from the gateway may supply the caller's IP via `X-Forwarded-For`.
    """
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


def client_ip(request: Request) -> str | None:
    """The caller's IP for audit rows; `X-Forwarded-For` only from the verified gateway."""
    forwarded = getattr(request.state, "forwarded_client_ip", None)
    if forwarded:
        return str(forwarded)
    return request.client.host if request.client else None


# --- Request-scoped database access ----------------------------------------------------


async def get_repository(
    request: Request, principal: CurrentPrincipal
) -> AsyncIterator[MetadataRepository]:
    """A repository bound to the authenticated caller's tenant (Section 19)."""
    async with tenant_scope(get_session_factory(request), uuid.UUID(principal.tenant_id)) as db:
        yield MetadataRepository(db)
        await db.commit()


ScopedRepo = Annotated[MetadataRepository, Depends(get_repository, scope="function")]


async def load_data_source_tenant_id(
    data_source_id: uuid.UUID, repository: ScopedRepo
) -> uuid.UUID | None:
    """The Section 7.2 `load_resource_tenant_id` callable: the owning tenant, nothing else."""
    return await repository.get_data_source_tenant_id(data_source_id)


# --- Service factories ------------------------------------------------------------------


def build_data_source_service(
    request: Request, repository: MetadataRepository
) -> DataSourceService:
    return DataSourceService(
        repository=repository,
        secrets=get_secret_store(request),
        connectors=get_connectors(request),
        egress=get_egress_policy(request),
        audit=get_audit_sink(request),
        vault_mount=get_app_settings(request).vault_mount,
    )


def build_catalog_sync_service(
    request: Request, repository: MetadataRepository
) -> CatalogSyncService:
    return CatalogSyncService(
        repository=repository,
        secrets=get_secret_store(request),
        connectors=get_connectors(request),
        events=request.app.state.events,
    )
