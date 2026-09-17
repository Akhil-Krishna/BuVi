"""The MinIO result store creates its bucket with the TTL lifecycle rule (Sections 13, 28)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest

from query_gateway.infrastructure.storage.minio_store import LIFECYCLE_RULE_ID, MinioResultStore

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def minio() -> Iterator[dict[str, str]]:
    from testcontainers.minio import MinioContainer

    with MinioContainer("quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z") as container:
        config = container.get_config()
        yield {
            "endpoint": config["endpoint"],
            "access_key": config["access_key"],
            "secret_key": config["secret_key"],
        }


async def test_put_creates_bucket_with_lifecycle_and_stores_object(minio: dict[str, str]) -> None:
    store = MinioResultStore(
        endpoint=minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
        secure=False,
        bucket="query-results",
        ttl_days=1,
    )
    tenant, query = uuid.uuid4(), uuid.uuid4()
    stored = await store.put(tenant_id=tenant, query_id=query, payload=b'{"rows": [[1]]}')
    assert stored.handle == f"s3://query-results/tenants/{tenant}/queries/{query}.json"
    assert await store.ping()

    client = store._client
    body = client.get_object("query-results", f"tenants/{tenant}/queries/{query}.json")
    try:
        assert json.loads(body.read()) == {"rows": [[1]]}
    finally:
        body.close()
        body.release_conn()
    rules = client.get_bucket_lifecycle("query-results").rules
    assert [(r.rule_id, r.expiration.days) for r in rules] == [(LIFECYCLE_RULE_ID, 1)]

    assert await store.get(tenant_id=tenant, query_id=query) == b'{"rows": [[1]]}'
    assert await store.get(tenant_id=tenant, query_id=uuid.uuid4()) is None


async def test_unreachable_store_raises_without_detail() -> None:
    from query_gateway.infrastructure.storage.base import ResultStoreError

    store = MinioResultStore(
        endpoint="127.0.0.1:1",
        access_key="a",
        secret_key="b",
        secure=False,
        bucket="query-results",
        ttl_days=1,
    )
    with pytest.raises(ResultStoreError) as info:
        await store.put(tenant_id=uuid.uuid4(), query_id=uuid.uuid4(), payload=b"{}")
    assert str(info.value) == ""
    with pytest.raises(ResultStoreError):
        await store.get(tenant_id=uuid.uuid4(), query_id=uuid.uuid4())
    assert not await store.ping()
