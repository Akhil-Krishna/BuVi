"""Secret storage by opaque reference (Sections 8.1, 13.1, 24).

Contract: `SecretStore`, `VaultSecretStore`, `InMemorySecretStore`, `SecretStoreError`,
`vault_kv2_path`. No secret is ever logged, returned in an error, or written anywhere
but the secret backend.
"""

from platform_secrets.store import (
    InMemorySecretStore,
    SecretStore,
    SecretStoreError,
    VaultSecretStore,
    validate_ref,
    vault_kv2_path,
)

__all__ = [
    "InMemorySecretStore",
    "SecretStore",
    "SecretStoreError",
    "VaultSecretStore",
    "validate_ref",
    "vault_kv2_path",
]
