"""Secret references owned by identity-service (Sections 6.6, 8.1, 24).

Postgres stores *references* -- `sessions.idp_refresh_token_ref`,
`mfa_credentials.secret_ref` -- and only the secret store turns one into a value.
The store itself is the shared `platform_secrets` client, extracted in Phase A3 when
metadata-service needed the same behaviour for data-source credentials (ADR 0002
item 13, ADR 0004). This module keeps what is identity-specific: the reference layout.
"""

from __future__ import annotations

import uuid

from platform_secrets import (
    InMemorySecretStore,
    SecretStore,
    SecretStoreError,
    VaultSecretStore,
)

__all__ = [
    "InMemorySecretStore",
    "SecretStore",
    "SecretStoreError",
    "VaultSecretStore",
    "mfa_secret_ref",
    "session_token_ref",
]


def session_token_ref(tenant_id: uuid.UUID, session_id: uuid.UUID) -> str:
    """Vault path for a session's IdP refresh token (Section 8.1)."""
    return f"tenants/{tenant_id}/sessions/{session_id}"


def mfa_secret_ref(tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """Vault path for a user's TOTP shared secret (Section 6.6)."""
    return f"tenants/{tenant_id}/users/{user_id}/mfa/totp"
