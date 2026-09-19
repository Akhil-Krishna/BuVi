"""The gateway's per-request checks, in order (Sections 6, 7, 20, 24).

1. per-IP rate limit (strict `auth` tier for login-shaped routes)   -> 429
2. public route?  done.
3. authenticate the forwarded credential via identity-service       -> 401
4. per-user and per-tenant rate limits                              -> 429
5. coarse permission / role from the Section 9 catalog               -> 403
6. Section 7.3 step-up freshness where the catalog demands it       -> 403

Rate limiting before authentication means a credential-stuffing burst is refused
before it costs an introspection call.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from fastapi import Request

from api_gateway.core.config import Settings
from api_gateway.domain.catalog import RouteSpec
from api_gateway.domain.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    PayloadTooLargeError,
    RateLimitedError,
)
from api_gateway.domain.policies.rate_limit import Bucket, RateLimitPolicy
from api_gateway.infrastructure.cache.rate_limiter import RateLimiter
from api_gateway.infrastructure.http.identity_client import IdentityClient
from platform_auth import Principal, StepUpRequiredError

BEARER = "Bearer "


@dataclass(frozen=True)
class AuthorizedRequest:
    principal: Principal | None
    client_ip: str


def client_ip_of(request: Request, trusted_proxy_hops: int) -> str:
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy_hops <= 0:
        return peer
    chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if len(chain) < trusted_proxy_hops:
        return peer
    try:
        return str(ipaddress.ip_address(chain[-trusted_proxy_hops]))
    except ValueError:
        return peer


def credentials_of(request: Request, cookie_name: str) -> tuple[str | None, str | None]:
    """Session cookie first, then `Authorization: Bearer <api key>` (Section 6.8)."""
    cookie = request.cookies.get(cookie_name)
    if cookie:
        return cookie, None
    authorization = request.headers.get("authorization", "")
    if authorization.startswith(BEARER):
        return None, authorization[len(BEARER) :].strip() or None
    return None, None


async def _limit(limiter: RateLimiter, buckets: list[Bucket]) -> None:
    decision = await limiter.consume(buckets)
    if not decision.allowed:
        raise RateLimitedError(
            headers={"Retry-After": str(decision.retry_after_seconds)}, scope=decision.scope
        )


async def authorize(
    request: Request,
    route: RouteSpec,
    *,
    settings: Settings,
    policy: RateLimitPolicy,
    limiter: RateLimiter,
    identity: IdentityClient,
) -> AuthorizedRequest:
    client_ip = client_ip_of(request, settings.trusted_proxy_hops)
    await _limit(limiter, policy.before_auth(rate_tier=route.rate_tier, client_ip=client_ip))
    if route.public:
        return AuthorizedRequest(principal=None, client_ip=client_ip)

    session_token, api_key = credentials_of(request, settings.session_cookie_name)
    if not session_token and not api_key:
        raise AuthenticationRequiredError()
    principal = await identity.introspect(session_token=session_token, api_key=api_key)
    request.state.principal = principal

    await _limit(
        limiter, policy.after_auth(tenant_id=principal.tenant_id, user_id=principal.user_id)
    )

    if route.permission and not principal.has_permission(route.permission):
        raise ForbiddenError()
    if route.any_permission and not any(principal.has_permission(p) for p in route.any_permission):
        raise ForbiddenError()
    if route.role and route.role not in principal.roles and not principal.is_platform_operator:
        raise ForbiddenError()
    if route.step_up and not principal.step_up_is_fresh():
        raise StepUpRequiredError.for_principal(principal)
    return AuthorizedRequest(principal=principal, client_ip=client_ip)


async def read_body(request: Request, limit: int) -> bytes:
    """Buffer the body, refusing anything over `limit` before it is fully read."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise PayloadTooLargeError()
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise PayloadTooLargeError()
    return bytes(body)
