"""Secret storage behind a narrow contract (Sections 8.1, 13.1, 24).

Postgres stores *references* -- `sessions.idp_refresh_token_ref`,
`mfa_credentials.secret_ref` -- and this module is the only thing that can turn
a reference into a value. Nothing else in the service reads a secret, and no
secret is ever logged, returned in a response, or written into an audit diff.

Phase A3 extracts a shared client into `packages/python/platform-*` when
metadata-service needs the same behaviour for data-source credentials; the
`SecretStore` protocol here is deliberately the shape that extraction will take.
See ADR 0002.
"""

from __future__ import annotations

import uuid
from typing import Protocol

import httpx

from identity_service.core.config import Settings


class SecretStore(Protocol):
    """Read, write and delete secrets by opaque reference."""

    async def write(self, ref: str, value: dict[str, str]) -> None: ...

    async def read(self, ref: str) -> dict[str, str] | None: ...

    async def delete(self, ref: str) -> None: ...


def session_token_ref(tenant_id: uuid.UUID, session_id: uuid.UUID) -> str:
    """Vault path for a session's IdP refresh token (Section 8.1)."""
    return f"tenants/{tenant_id}/sessions/{session_id}"


def mfa_secret_ref(tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """Vault path for a user's TOTP shared secret (Section 6.6)."""
    return f"tenants/{tenant_id}/users/{user_id}/mfa/totp"


class VaultSecretStore:
    """HashiCorp Vault KV v2 adapter.

    Errors deliberately carry no response body: a Vault error can echo the path
    and policy that failed, which is exactly the kind of detail Section 21 keeps
    out of anything a caller might see.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._client = client
        self._mount = settings.vault_mount
        self._token = settings.vault_token.get_secret_value()
        self._base = settings.vault_addr.rstrip("/")

    def _data_url(self, ref: str) -> str:
        return f"{self._base}/v1/{self._mount}/data/{ref}"

    def _metadata_url(self, ref: str) -> str:
        return f"{self._base}/v1/{self._mount}/metadata/{ref}"

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Vault-Token": self._token}

    async def write(self, ref: str, value: dict[str, str]) -> None:
        response = await self._client.post(
            self._data_url(ref), json={"data": value}, headers=self._headers
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Vault write failed with status {response.status_code}")

    async def read(self, ref: str) -> dict[str, str] | None:
        response = await self._client.get(self._data_url(ref), headers=self._headers)
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        if response.status_code >= 400:
            raise RuntimeError(f"Vault read failed with status {response.status_code}")
        payload = response.json()
        data = payload.get("data", {}).get("data")
        return dict(data) if isinstance(data, dict) else None

    async def delete(self, ref: str) -> None:
        response = await self._client.delete(self._metadata_url(ref), headers=self._headers)
        if response.status_code >= 400 and response.status_code != httpx.codes.NOT_FOUND:
            raise RuntimeError(f"Vault delete failed with status {response.status_code}")


class InMemorySecretStore:
    """Process-local secret store for tests.

    Refused outside dev/test by `Settings.assert_production_safe`.
    """

    def __init__(self) -> None:
        self._values: dict[str, dict[str, str]] = {}

    async def write(self, ref: str, value: dict[str, str]) -> None:
        self._values[ref] = dict(value)

    async def read(self, ref: str) -> dict[str, str] | None:
        stored = self._values.get(ref)
        return dict(stored) if stored is not None else None

    async def delete(self, ref: str) -> None:
        self._values.pop(ref, None)
