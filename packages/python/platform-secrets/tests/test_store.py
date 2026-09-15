"""platform-secrets: reference validation, Vault KV v2 wire behaviour, and error hygiene."""

from __future__ import annotations

import json

import httpx
import pytest

from platform_secrets import (
    InMemorySecretStore,
    SecretStoreError,
    VaultSecretStore,
    validate_ref,
    vault_kv2_path,
)

pytestmark = pytest.mark.unit

TOKEN = "vault-token-value"
REF = "tenants/t1/datasources/d1"


def _store(handler: httpx.MockTransport) -> tuple[VaultSecretStore, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=handler)
    return VaultSecretStore(addr="http://vault:8200/", token=TOKEN, mount="secret", http=http), http


@pytest.mark.parametrize(
    "ref", ["", "/abs/path", "a/../b", "a//b", "a/b/", "tenants/t1 /x", "a/b?x=1", "x" * 600]
)
def test_unsafe_references_are_refused(ref: str) -> None:
    with pytest.raises(ValueError, match="invalid secret reference"):
        validate_ref(ref)


def test_kv2_path_matches_section_8_2_form() -> None:
    assert vault_kv2_path("secret", REF) == "secret/data/tenants/t1/datasources/d1"


async def test_write_read_delete_use_kv2_urls_and_token_header() -> None:
    seen: list[httpx.Request] = []
    stored: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            stored.update(json.loads(request.content)["data"])
            return httpx.Response(200, json={})
        if request.method == "GET":
            return httpx.Response(200, json={"data": {"data": stored, "metadata": {}}})
        return httpx.Response(204)

    store, http = _store(httpx.MockTransport(handler))
    async with http:
        await store.write(REF, {"password": "p"})
        assert await store.read(REF) == {"password": "p"}
        await store.delete(REF)

    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", "/v1/secret/data/tenants/t1/datasources/d1"),
        ("GET", "/v1/secret/data/tenants/t1/datasources/d1"),
        ("DELETE", "/v1/secret/metadata/tenants/t1/datasources/d1"),
    ]
    assert all(r.headers["x-vault-token"] == TOKEN for r in seen)


async def test_missing_secret_reads_as_none() -> None:
    store, http = _store(httpx.MockTransport(lambda _r: httpx.Response(404, json={"errors": []})))
    async with http:
        assert await store.read(REF) is None
        await store.delete(REF)  # deleting something absent is not an error


async def test_backend_errors_never_carry_the_response_body_or_url() -> None:
    body = {"errors": ["permission denied on path secret/data/tenants/t1 for token " + TOKEN]}
    store, http = _store(httpx.MockTransport(lambda _r: httpx.Response(403, json=body)))
    async with http:
        for call in (store.read(REF), store.write(REF, {"k": "v"}), store.delete(REF)):
            with pytest.raises(SecretStoreError) as info:
                await call
            assert TOKEN not in str(info.value)
            assert "tenants" not in str(info.value)


async def test_unreachable_backend_raises_without_url_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    store, http = _store(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(SecretStoreError) as info:
            await store.read(REF)
        assert "vault" not in str(info.value)
        assert info.value.__cause__ is None
        assert not await store.ping()


async def test_ping_accepts_serving_vault_states() -> None:
    store, http = _store(httpx.MockTransport(lambda _r: httpx.Response(429)))
    async with http:
        assert await store.ping()


async def test_in_memory_store_copies_values_and_validates_refs() -> None:
    store = InMemorySecretStore()
    value = {"password": "p"}
    await store.write(REF, value)
    value["password"] = "mutated"
    assert await store.read(REF) == {"password": "p"}
    assert store.refs() == frozenset({REF})
    with pytest.raises(ValueError):
        await store.read("../escape")
    await store.delete(REF)
    assert await store.read(REF) is None
