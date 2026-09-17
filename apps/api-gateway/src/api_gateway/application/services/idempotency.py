"""Section 9 Idempotency-Key at the gateway: begin before proxying, record after.

Runs *after* `authorize` on every request, so a replay never bypasses authentication, permission,
step-up or rate limits, and records are scoped to the authenticated principal.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Request, Response

from api_gateway.domain.catalog import RouteSpec
from api_gateway.domain.errors import (
    IdempotencyInProgressError,
    IdempotencyKeyInvalidError,
    IdempotencyKeyReusedError,
    IdempotencyUnavailableError,
    IdempotentReplayUnavailableError,
)
from api_gateway.domain.policies.idempotency import fingerprint, is_final, record_key, valid_key
from api_gateway.infrastructure.cache.idempotency_store import (
    IdempotencyStore,
    IdempotencyStoreError,
    StoredResponse,
)
from platform_auth import Principal

HEADER = "idempotency-key"
REPLAYED_HEADER = "Idempotent-Replayed"


@dataclass
class IdempotencyGuard:
    """A started idempotent request. `replay` is set when the stored response answers it."""

    store: IdempotencyStore | None = None
    key: str = ""
    token: str = ""
    fingerprint: str = ""
    store_body: bool = True
    replay: Response | None = None

    @property
    def active(self) -> bool:
        return self.store is not None and self.replay is None

    async def finish(self, response: Response, *, ttl_ms: int, max_body_bytes: int) -> None:
        if not self.active or self.store is None:
            return
        if not is_final(response.status_code):
            await self.store.release(self.key, self.token)
            return
        body: bytes | None = bytes(response.body)
        if (
            not self.store_body
            or "set-cookie" in response.headers
            or len(body or b"") > max_body_bytes
        ):
            body = None
        await self.store.complete(
            self.key,
            self.token,
            self.fingerprint,
            StoredResponse(response.status_code, response.headers.get("content-type"), body),
            ttl_ms,
        )

    async def abandon(self) -> None:
        if self.active and self.store is not None:
            await self.store.release(self.key, self.token)


async def begin(
    request: Request,
    route: RouteSpec,
    principal: Principal | None,
    body: bytes,
    *,
    store: IdempotencyStore,
    lock_seconds: float,
) -> IdempotencyGuard:
    key = request.headers.get(HEADER)
    if key is None or route.idempotency_mode == "ignore" or principal is None:
        return IdempotencyGuard()
    if not valid_key(key):
        raise IdempotencyKeyInvalidError()
    guard = IdempotencyGuard(
        store=store,
        key=record_key(principal.tenant_id, principal.user_id, key),
        token=secrets.token_hex(16),
        fingerprint=fingerprint(request.method, request.url.path, request.url.query, body),
        store_body=route.idempotency_mode == "replay",
    )
    try:
        existing = await store.begin(
            guard.key, guard.fingerprint, guard.token, int(lock_seconds * 1000)
        )
    except IdempotencyStoreError:
        raise IdempotencyUnavailableError(headers={"Retry-After": "5"}) from None
    if existing is None:
        return guard
    if existing.fingerprint != guard.fingerprint:
        raise IdempotencyKeyReusedError()
    if existing.state == "pending" or existing.response is None:
        raise IdempotencyInProgressError(headers={"Retry-After": "2"})
    if existing.response.body is None:
        raise IdempotentReplayUnavailableError()
    replay = Response(
        content=existing.response.body,
        status_code=existing.response.status_code,
        headers={REPLAYED_HEADER: "true"},
    )
    if existing.response.content_type:
        replay.headers["content-type"] = existing.response.content_type
    guard.replay = replay
    return guard
