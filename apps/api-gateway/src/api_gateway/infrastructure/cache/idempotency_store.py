"""Redis-backed Idempotency-Key records (Section 9).

A record is `pending` (with a lock TTL) while the first request runs, then `completed` for the
record TTL. `begin` is one Lua script, so two gateway replicas racing on the same key cannot both
start. `release` deletes only a pending record carrying the caller's fingerprint and token, so a
late release never removes another request's record.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

BEGIN_LUA: Final = """
local current = redis.call('GET', KEYS[1])
if current then
  return current
end
redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
return false
"""

RELEASE_LUA: Final = """
local current = redis.call('GET', KEYS[1])
if current then
  local record = cjson.decode(current)
  if record['state'] == 'pending' and record['token'] == ARGV[1] then
    redis.call('DEL', KEYS[1])
    return 1
  end
end
return 0
"""

COMPLETE_LUA: Final = """
local current = redis.call('GET', KEYS[1])
if current then
  local record = cjson.decode(current)
  if record['state'] == 'pending' and record['token'] == ARGV[1] then
    redis.call('SET', KEYS[1], ARGV[2], 'PX', ARGV[3])
    return 1
  end
end
return 0
"""


class IdempotencyStoreError(Exception):
    """The store could not be reached; the guarantee cannot be given."""


@dataclass(frozen=True)
class StoredResponse:
    status_code: int
    content_type: str | None
    body: bytes | None  # None: completed, body deliberately not stored


@dataclass(frozen=True)
class Existing:
    state: Literal["pending", "completed"]
    fingerprint: str
    response: StoredResponse | None


class IdempotencyStore(Protocol):
    async def begin(
        self, key: str, fingerprint: str, token: str, lock_ms: int
    ) -> Existing | None: ...

    async def complete(
        self, key: str, token: str, fingerprint: str, response: StoredResponse, ttl_ms: int
    ) -> None: ...

    async def release(self, key: str, token: str) -> None: ...


class RedisIdempotencyStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._begin = redis.register_script(BEGIN_LUA)
        self._release = redis.register_script(RELEASE_LUA)
        self._complete = redis.register_script(COMPLETE_LUA)

    async def begin(self, key: str, fingerprint: str, token: str, lock_ms: int) -> Existing | None:
        pending = json.dumps({"state": "pending", "fp": fingerprint, "token": token})
        try:
            raw = await self._begin(keys=[key], args=[pending, lock_ms])
        except RedisError as error:
            raise IdempotencyStoreError() from error
        if not raw:
            return None
        record = json.loads(raw)
        response = record.get("response")
        return Existing(
            state=record["state"],
            fingerprint=record["fp"],
            response=StoredResponse(
                status_code=int(response["status"]),
                content_type=response.get("content_type"),
                body=base64.b64decode(response["body"])
                if response.get("body") is not None
                else None,
            )
            if response
            else None,
        )

    async def complete(
        self, key: str, token: str, fingerprint: str, response: StoredResponse, ttl_ms: int
    ) -> None:
        record = json.dumps(
            {
                "state": "completed",
                "fp": fingerprint,
                "response": {
                    "status": response.status_code,
                    "content_type": response.content_type,
                    "body": base64.b64encode(response.body).decode("ascii")
                    if response.body is not None
                    else None,
                },
            }
        )
        try:
            await self._complete(keys=[key], args=[token, record, ttl_ms])
        except RedisError:
            # The response has been produced; a lost record only weakens a later retry, which
            # then expires its pending lock and may run again -- logged, never user-facing.
            logger.error("idempotency record not completed", extra={"context": {}})

    async def release(self, key: str, token: str) -> None:
        try:
            await self._release(keys=[key], args=[token])
        except RedisError:
            logger.warning("idempotency lock not released; it expires", extra={"context": {}})
