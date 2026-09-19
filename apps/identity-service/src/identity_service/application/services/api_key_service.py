"""API keys for service accounts and automation (Section 6.8).

The full key is returned exactly once, at creation, and only its argon2id hash
is stored. Authentication narrows candidates by the non-secret `key_prefix`
first, so verifying an inbound key costs one hash rather than one per key.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from identity_service.application.services import audit_service as events
from identity_service.application.services.audit_service import AuditService
from identity_service.core.config import Settings
from identity_service.domain.errors import NotFoundError
from identity_service.domain.policies.tenant_policy import effective_permissions
from identity_service.domain.value_objects.tokens import (
    API_KEY_PREFIX_RANDOM_CHARS,
    generate_api_key,
    hash_secret,
    verify_secret,
)
from identity_service.infrastructure.db.models import ApiKey
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)
from platform_auth import Principal


@dataclass(frozen=True)
class IssuedApiKey:
    api_key: ApiKey
    #: Shown once at creation and never recoverable (Section 6.8).
    secret: str


class ApiKeyService:
    def __init__(
        self,
        *,
        repository: IdentityRepository,
        audit: AuditService,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._settings = settings

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        name: str,
        scopes: frozenset[str],
        expires_at: dt.datetime | None,
        ip_address: str | None = None,
    ) -> IssuedApiKey:
        """Mint a key owned by the calling user."""
        full_key, key_prefix = generate_api_key(self._settings.api_key_prefix)
        api_key = await self._repository.add_api_key(
            ApiKey(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                owner_user_id=actor_user_id,
                name=name,
                key_prefix=key_prefix,
                secret_hash=hash_secret(full_key),
                scopes=sorted(scopes),
                expires_at=expires_at,
                created_by=actor_user_id,
            )
        )
        await self._audit.record(
            event_type=events.EVENT_API_KEY_CREATED,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            resource_type="api_key",
            resource_id=str(api_key.id),
            # `key_prefix` is the non-secret display value; the key itself is
            # never written to the audit log (Section 24).
            after_state={
                "name": name,
                "key_prefix": key_prefix,
                "scopes": sorted(scopes),
                "expires_at": str(expires_at) if expires_at else None,
            },
            ip_address=ip_address,
        )
        return IssuedApiKey(api_key=api_key, secret=full_key)

    async def revoke(
        self,
        *,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        api_key_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> None:
        """Revoke a key immediately (Section 6.8)."""
        revoked = await self._repository.revoke_api_key(
            tenant_id, api_key_id, dt.datetime.now(dt.UTC)
        )
        if not revoked:
            raise NotFoundError()
        await self._audit.record(
            event_type=events.EVENT_API_KEY_REVOKED,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            resource_type="api_key",
            resource_id=str(api_key_id),
            ip_address=ip_address,
        )

    async def authenticate(self, presented_key: str) -> Principal | None:
        """Resolve a presented API key to a `Principal`, or `None`.

        Scopes are the key's explicit allow-list (Section 7.1: a service account
        never inherits a human role's permissions). When the key belongs to a
        human owner, its effective permissions are the intersection of the
        requested scopes with what that user can actually do -- a key cannot be
        used to exceed its owner's own authority.
        """
        prefix_length = len(self._settings.api_key_prefix) + API_KEY_PREFIX_RANDOM_CHARS
        if len(presented_key) <= prefix_length:
            return None
        candidates = await self._repository.find_api_keys_by_prefix(presented_key[:prefix_length])

        for candidate in candidates:
            if not verify_secret(presented_key, candidate.secret_hash):
                continue
            await self._repository.bind_tenant(candidate.tenant_id)
            granted = frozenset(candidate.scopes)
            if candidate.owner_user_id is not None:
                owner_roles = await self._repository.get_user_role_keys(candidate.owner_user_id)
                policies = await self._repository.get_policies(candidate.tenant_id)
                granted &= effective_permissions(owner_roles, policies)
            await self._repository.touch_api_key(candidate.id, dt.datetime.now(dt.UTC))
            return Principal(
                user_id=str(candidate.owner_user_id or candidate.id),
                tenant_id=str(candidate.tenant_id),
                permissions=granted,
                auth_method="api_key",
                # Section 7.3 step-up is a human, interactive control; an API
                # key can never satisfy it and must not reach those endpoints.
                mfa_verified=False,
                session_id=None,
            )
        return None
