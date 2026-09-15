"""MinIO/S3 result store. Objects expire through a bucket lifecycle rule (Section 28: "lifecycle
rules matching the result-handle TTL"), not through application cleanup that could be skipped."""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
import uuid

import urllib3
from minio import Minio
from minio.commonconfig import ENABLED, Filter
from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule

from query_gateway.infrastructure.storage.base import ResultStoreError, StoredResult, result_key

logger = logging.getLogger(__name__)

LIFECYCLE_RULE_ID = "query-result-ttl"


class MinioResultStore:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool,
        bucket: str,
        ttl_days: int,
    ) -> None:
        # No client-side retries and bounded timeouts: an outage must surface as a fast
        # RESULT_STORE_UNAVAILABLE, not a request stuck behind retry back-off.
        http = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=3.0, read=30.0), retries=urllib3.Retry(total=0)
        )
        self._client = Minio(
            endpoint, access_key=access_key, secret_key=secret_key, secure=secure, http_client=http
        )
        self._bucket = bucket
        self._ttl_days = ttl_days
        self._ready = False
        self._lock = asyncio.Lock()

    def _ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
        self._client.set_bucket_lifecycle(
            self._bucket,
            LifecycleConfig(
                [
                    Rule(
                        ENABLED,
                        rule_filter=Filter(prefix="tenants/"),
                        rule_id=LIFECYCLE_RULE_ID,
                        expiration=Expiration(days=self._ttl_days),
                    )
                ]
            ),
        )

    async def _ready_bucket(self) -> None:
        if self._ready:
            return
        async with self._lock:
            if not self._ready:
                await asyncio.to_thread(self._ensure_bucket)
                self._ready = True

    async def put(
        self, *, tenant_id: uuid.UUID, query_id: uuid.UUID, payload: bytes
    ) -> StoredResult:
        key = result_key(tenant_id, query_id)
        try:
            await self._ready_bucket()
            await asyncio.to_thread(
                self._client.put_object,
                self._bucket,
                key,
                io.BytesIO(payload),
                len(payload),
                content_type="application/json",
            )
        except Exception as exc:
            # Any client failure; the message (which names the endpoint and bucket) is dropped.
            logger.warning(
                "result store write failed", extra={"context": {"error_type": type(exc).__name__}}
            )
            raise ResultStoreError() from None
        return StoredResult(
            handle=f"s3://{self._bucket}/{key}",
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=self._ttl_days),
        )

    async def ping(self) -> bool:
        try:
            await asyncio.to_thread(self._client.bucket_exists, self._bucket)
        except Exception:
            return False
        return True
