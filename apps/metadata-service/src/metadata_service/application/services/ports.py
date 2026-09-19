"""Outbound ports of metadata-service's application layer."""

from __future__ import annotations

from typing import Protocol

from platform_contracts import MetadataSyncCompleted


class MetadataEvents(Protocol):
    """Section 18.1 events metadata-service produces. Best effort: the sync is committed
    whether or not its event is published."""

    async def sync_completed(self, event: MetadataSyncCompleted) -> None: ...
