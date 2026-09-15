"""Secret storage behind a narrow contract (Sections 8.1, 13.1, 24).

Postgres rows hold references (`metadata.data_sources.secret_ref`,
`identity.sessions.idp_refresh_token_ref`); this module is the only code that turns a
reference into a value. Extracted from identity-service in Phase A3, when
metadata-service needed the same behaviour for data-source credentials (ADR 0002
item 13, ADR 0004).
"""

from __future__ import annotations

import re
from typing import Final, Protocol

import httpx

_SEGMENT: Final = re.compile(r"^[A-Za-z0-9_\-]+$")
_MAX_REF_LENGTH: Final = 512


class SecretStoreError(RuntimeError):
    """The secret backend failed.

    The message never carries a backend response body: a Vault error can echo the
    path and policy that failed, which Section 21 keeps away from callers.
    """


def validate_ref(ref: str) -> str:
    """Return `ref` if it is a safe relative path, else raise `ValueError`.

    A reference is built by the owning service, never taken from a request. Checking
    it anyway means a bug upstream cannot turn into a read of another tenant's path
    via `..` or an absolute path.
    """
    if not ref or len(ref) > _MAX_REF_LENGTH:
        raise ValueError("invalid secret reference")
    if not all(_SEGMENT.match(segment) for segment in ref.split("/")):
        raise ValueError("invalid secret reference")
    return ref


def vault_kv2_path(mount: str, ref: str) -> str:
    """The Vault API path of a KV v2 secret: `<mount>/data/<ref>` (Section 8.2)."""
    return f"{validate_ref(mount)}/data/{validate_ref(ref)}"


class SecretStore(Protocol):
    """Read, write and delete secrets by opaque reference."""

    async def write(self, ref: str, value: dict[str, str]) -> None: ...

    async def read(self, ref: str) -> dict[str, str] | None: ...

    async def delete(self, ref: str) -> None: ...

    async def ping(self) -> bool: ...


class VaultSecretStore:
    """HashiCorp Vault KV v2 adapter."""

    def __init__(self, *, addr: str, token: str, mount: str, http: httpx.AsyncClient) -> None:
        self._base = addr.rstrip("/")
        self._token = token
        self._mount = validate_ref(mount)
        self._http = http

    def _data_url(self, ref: str) -> str:
        return f"{self._base}/v1/{vault_kv2_path(self._mount, ref)}"

    def _metadata_url(self, ref: str) -> str:
        return f"{self._base}/v1/{self._mount}/metadata/{validate_ref(ref)}"

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Vault-Token": self._token}

    async def write(self, ref: str, value: dict[str, str]) -> None:
        url = self._data_url(ref)
        try:
            response = await self._http.post(url, json={"data": value}, headers=self._headers)
        except httpx.HTTPError:
            # `from None`: an httpx error message carries the URL, i.e. the secret's path.
            raise SecretStoreError("secret backend unreachable") from None
        if response.status_code >= 400:
            raise SecretStoreError(f"secret write failed with status {response.status_code}")

    async def read(self, ref: str) -> dict[str, str] | None:
        url = self._data_url(ref)
        try:
            response = await self._http.get(url, headers=self._headers)
        except httpx.HTTPError:
            # `from None`: an httpx error message carries the URL, i.e. the secret's path.
            raise SecretStoreError("secret backend unreachable") from None
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        if response.status_code >= 400:
            raise SecretStoreError(f"secret read failed with status {response.status_code}")
        try:
            data = response.json().get("data", {}).get("data")
        except ValueError:
            raise SecretStoreError("secret backend returned an unreadable body") from None
        if not isinstance(data, dict):
            return None
        return {str(key): str(item) for key, item in data.items()}

    async def delete(self, ref: str) -> None:
        url = self._metadata_url(ref)
        try:
            response = await self._http.delete(url, headers=self._headers)
        except httpx.HTTPError:
            # `from None`: an httpx error message carries the URL, i.e. the secret's path.
            raise SecretStoreError("secret backend unreachable") from None
        if response.status_code >= 400 and response.status_code != httpx.codes.NOT_FOUND:
            raise SecretStoreError(f"secret delete failed with status {response.status_code}")

    async def ping(self) -> bool:
        try:
            response = await self._http.get(f"{self._base}/v1/sys/health", timeout=2.0)
        except httpx.HTTPError:
            return False
        # 200 active; 429 standby; 472/473 performance/DR secondaries -- all serving.
        return response.status_code in (200, 429, 472, 473)


class InMemorySecretStore:
    """Process-local secret store for tests. Services refuse it outside dev/test."""

    def __init__(self) -> None:
        self._values: dict[str, dict[str, str]] = {}

    async def write(self, ref: str, value: dict[str, str]) -> None:
        self._values[validate_ref(ref)] = dict(value)

    async def read(self, ref: str) -> dict[str, str] | None:
        stored = self._values.get(validate_ref(ref))
        return dict(stored) if stored is not None else None

    async def delete(self, ref: str) -> None:
        self._values.pop(validate_ref(ref), None)

    async def ping(self) -> bool:
        return True

    def refs(self) -> frozenset[str]:
        """Stored references, for test assertions."""
        return frozenset(self._values)
