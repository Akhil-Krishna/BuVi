"""Builds this service's secret store from settings (Section 13.1).

The client is the shared `platform_secrets` package; data-source credentials live at
`data_source_secret_ref(tenant_id, data_source_id)` under the configured KV v2 mount.
"""

from __future__ import annotations

import httpx

from metadata_service.core.config import Settings
from platform_secrets import InMemorySecretStore, SecretStore, VaultSecretStore


def build_secret_store(settings: Settings, http: httpx.AsyncClient) -> SecretStore:
    if settings.vault_use_memory_stub:
        return InMemorySecretStore()
    return VaultSecretStore(
        addr=settings.vault_addr,
        token=settings.vault_token.get_secret_value(),
        mount=settings.vault_mount,
        http=http,
    )
