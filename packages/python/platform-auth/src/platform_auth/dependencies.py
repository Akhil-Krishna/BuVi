"""FastAPI authorization dependencies (Section 7.2).

Three layers, composed per endpoint and never substituted for one another:

1. `get_principal`     -- authenticate. Who is calling?
2. `require_permission`-- coarse RBAC. May this kind of caller do this?
3. `require_resource_owner` -- ABAC. Does *this* resource belong to the caller's
   tenant? Cross-tenant IDs answer `404`, never `403`, so a caller cannot
   enumerate valid resource IDs by response code (BOLA/IDOR defense).

Layer 2 alone is never sufficient on an endpoint that takes a resource ID. That
is the single most important rule in the build spec (Section 7.4).

`require_step_up` adds the Section 7.3 freshness check on top of 2 and 3.

Authenticating a caller is service-specific -- identity-service reads a session
cookie, other services validate a service JWT -- so each app installs its own
resolver at startup with `install_principal_resolver`, and these dependencies
stay identical everywhere.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import Depends, FastAPI, HTTPException, Request, status

from platform_auth.principal import Principal

#: Signature every service's authentication adapter must satisfy.
PrincipalResolver = Callable[[Request], Awaitable[Principal]]

#: Signature of the callable `require_resource_owner` uses to look up the owning
#: tenant of a path-addressed resource. Implementations commonly return a
#: `uuid.UUID` straight from the database, so the comparison below normalises.
TenantLoader = Callable[..., Awaitable[object | None]]

_RESOLVER_ATTR = "platform_auth_principal_resolver"


class SupportsAppState(Protocol):
    state: object


def install_principal_resolver(app: FastAPI, resolver: PrincipalResolver) -> None:
    """Bind a service's authentication adapter to its FastAPI application.

    Stored on `app.state` rather than in a module global so that two apps in one
    test process (a common pattern in contract tests) cannot resolve each
    other's principals.
    """
    setattr(app.state, _RESOLVER_ATTR, resolver)


async def get_principal(request: Request) -> Principal:
    """Authenticate the caller using the app's installed resolver."""
    resolver: PrincipalResolver | None = getattr(request.app.state, _RESOLVER_ATTR, None)
    if resolver is None:  # pragma: no cover - a wiring error, not a runtime path
        raise RuntimeError(
            "No principal resolver installed. Call "
            "platform_auth.install_principal_resolver(app, resolver) during startup."
        )
    return await resolver(request)


def require_permission(permission: str) -> Callable[..., Awaitable[Principal]]:
    """Require a coarse Section 7.1 permission.

    Never sufficient on its own for an endpoint that takes a resource ID --
    compose it with `require_resource_owner`.
    """

    async def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_permission(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return principal

    return dependency


def require_resource_owner(
    load_resource_tenant_id: TenantLoader,
) -> Callable[..., Awaitable[Principal]]:
    """Require that a path-addressed resource belongs to the caller's tenant.

    `load_resource_tenant_id` is an injected callable that loads *only* the
    resource's `tenant_id` from its path parameter -- it must not load the full
    object, because doing so creates a trust boundary that later code can read
    from while skipping this check.

    A resource that does not exist and a resource owned by another tenant are
    indistinguishable in the response: both are `404`.
    """

    async def dependency(
        principal: Principal = Depends(get_principal),
        resource_tenant_id: object | None = Depends(load_resource_tenant_id),
    ) -> Principal:
        if resource_tenant_id is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
        # Normalise before comparing. A loader that returns `uuid.UUID` and a
        # `Principal.tenant_id` that is a `str` are never equal, which would
        # turn every same-tenant request into a 404 -- a failure that looks
        # like correct BOLA defense while actually breaking the endpoint.
        if str(resource_tenant_id) != str(principal.tenant_id) and (
            not principal.is_platform_operator
        ):
            # 404, not 403 -- do not confirm the resource exists (Section 7.2).
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
        return principal

    return dependency


def require_step_up(principal: Principal = Depends(get_principal)) -> Principal:
    """Require an MFA verification inside the Section 7.3 freshness window.

    Applied on top of -- never instead of -- the permission and resource-tenant
    checks. Phase A10 extends this with WebAuthn and makes it mandatory for
    `platform_super_admin`; the freshness rule itself does not change.
    """
    if not principal.step_up_is_fresh():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Step-up authentication required",
            headers={"WWW-Authenticate": 'MFA realm="step-up", max_age=300'},
        )
    return principal
