"""Schema sync: introspect a data source and populate tables/columns/relationships.

Phase A3: "initially synchronous for MVP, moved to worker-runtime" later. Section 9
describes `POST /data-sources/{id}/sync` as enqueueing a job and Section 18.1 names the
`metadata.sync.requested`/`completed` events; until worker-runtime exists the job runs
inside the request and returns the `metadata.sync.completed` payload shape directly
(ADR 0004).
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from metadata_service.application.services.data_source_service import (
    connector_for,
    ensure_enabled,
    load_connection_target,
    tenant_of,
)
from metadata_service.application.services.ports import MetadataEvents
from metadata_service.domain.errors import NotFoundError
from metadata_service.domain.value_objects.connection import (
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_ERROR,
)
from metadata_service.domain.value_objects.diagnostics import (
    ConnectorError,
    DiagnosticCode,
    failure_message,
    synced_message,
)
from metadata_service.infrastructure.connectors.base import CatalogConnector
from metadata_service.infrastructure.db.models import DataSource
from metadata_service.infrastructure.db.repositories.metadata_repository import (
    MetadataRepository,
)
from platform_auth import Principal
from platform_contracts import MetadataSyncCompleted
from platform_observability import request_id_var
from platform_secrets import SecretStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncOutcome:
    data_source: DataSource
    ok: bool
    code: DiagnosticCode
    message: str
    tables_synced: int
    columns_synced: int
    relationships_synced: int
    snapshot_id: uuid.UUID | None
    synced_at: dt.datetime


class CatalogSyncService:
    def __init__(
        self,
        *,
        repository: MetadataRepository,
        secrets: SecretStore,
        connectors: Mapping[str, CatalogConnector],
        events: MetadataEvents,
    ) -> None:
        self._repository = repository
        self._secrets = secrets
        self._connectors = connectors
        self._events = events

    async def _lock(self, tenant_id: uuid.UUID, data_source_id: uuid.UUID) -> DataSource:
        data_source = await self._repository.get_data_source(
            tenant_id, data_source_id, for_update=True
        )
        if data_source is None:
            raise NotFoundError()
        return data_source

    async def sync(self, principal: Principal, data_source_id: uuid.UUID) -> SyncOutcome:
        tenant_id = tenant_of(principal)
        data_source = await self._repository.get_data_source(tenant_id, data_source_id)
        if data_source is None:
            raise NotFoundError()
        ensure_enabled(data_source)
        connector = connector_for(self._connectors, data_source)
        target = await load_connection_target(self._secrets, data_source)
        await self._repository.commit()

        try:
            catalog = await connector.introspect(target)
        except ConnectorError as exc:
            locked = await self._lock(tenant_id, data_source_id)
            if locked.status != STATUS_DISABLED:
                locked = await self._repository.set_status(locked, status=STATUS_ERROR)
            await self._repository.commit()
            await self._announce(principal, locked, succeeded=False, tables=0)
            return SyncOutcome(
                data_source=locked,
                ok=False,
                code=exc.code,
                message=failure_message(exc.code),
                tables_synced=0,
                columns_synced=0,
                relationships_synced=0,
                snapshot_id=None,
                synced_at=dt.datetime.now(dt.UTC),
            )

        # Concurrent syncs of one data source serialize on the row lock.
        locked = await self._lock(tenant_id, data_source_id)
        counts = await self._repository.apply_catalog(tenant_id, data_source_id, catalog)
        snapshot = await self._repository.add_snapshot(
            tenant_id, data_source_id, catalog.checksum()
        )
        locked = await self._repository.set_status(
            locked,
            status=locked.status if locked.status == STATUS_DISABLED else STATUS_ACTIVE,
            synced_at=snapshot.snapshot_at,
        )
        await self._repository.commit()
        await self._announce(principal, locked, succeeded=True, tables=counts.tables)
        return SyncOutcome(
            data_source=locked,
            ok=True,
            code=DiagnosticCode.SYNCED,
            message=synced_message(counts.tables, counts.columns),
            tables_synced=counts.tables,
            columns_synced=counts.columns,
            relationships_synced=counts.relationships,
            snapshot_id=snapshot.id,
            synced_at=snapshot.snapshot_at,
        )

    async def _announce(
        self, principal: Principal, data_source: DataSource, *, succeeded: bool, tables: int
    ) -> None:
        """`metadata.sync.completed`, after the commit. Best effort: a lost event costs the
        notification, never the sync."""
        try:
            await self._events.sync_completed(
                MetadataSyncCompleted(
                    tenant_id=data_source.tenant_id,
                    data_source_id=data_source.id,
                    data_source_name=data_source.name,
                    status="succeeded" if succeeded else "failed",
                    tables_synced=tables,
                    user_id=uuid.UUID(principal.user_id),
                    request_id=request_id_var.get(),
                )
            )
        except Exception as error:
            logger.warning(
                "metadata.sync.completed not published",
                extra={
                    "context": {
                        "data_source_id": str(data_source.id),
                        "error_type": type(error).__name__,
                    }
                },
            )
