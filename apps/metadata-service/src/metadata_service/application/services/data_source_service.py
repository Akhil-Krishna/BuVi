"""Data-source lifecycle: create, read, set credentials, connectivity test (Sections 9, 13.1).

Status (Section 8.2 `pending|active|error|disabled`), per ADR 0004:

* created -> `pending` ("create = pending until secret set", Section 9);
* credentials set or rotated -> `pending` again: new credentials are unverified;
* a successful test or sync -> `active`; a failed one -> `error`;
* `disabled` is never changed by these operations, which refuse to run on it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from metadata_service.domain.errors import (
    DataSourceDisabledError,
    DestinationNotAllowedError,
    EngineNotSupportedError,
    NotFoundError,
    SecretNotConfiguredError,
    SecretStoreUnavailableError,
)
from metadata_service.domain.value_objects.connection import (
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_ERROR,
    STATUS_PENDING,
    SUPPORTED_ENGINES,
    ConnectionSecret,
    ConnectionTarget,
    data_source_secret_ref,
)
from metadata_service.domain.value_objects.diagnostics import ConnectivityResult
from metadata_service.infrastructure.audit.sink import AuditRecord, AuditSink
from metadata_service.infrastructure.connectors.base import CatalogConnector
from metadata_service.infrastructure.db.models import DataSource
from metadata_service.infrastructure.db.repositories.metadata_repository import (
    MetadataRepository,
)
from platform_auth import Principal
from platform_egress import EgressPolicy
from platform_secrets import SecretStore, SecretStoreError, vault_kv2_path

EVENT_CONNECTION_CREATED: Final = "connection.created"
EVENT_CONNECTION_SECRET_ROTATED: Final = "connection.secret_rotated"  # noqa: S105 - event type
RESOURCE_TYPE: Final = "data_source"


@dataclass(frozen=True)
class NewDataSource:
    name: str
    engine: str
    host_label: str
    database_name: str
    allowed_schemas: tuple[str, ...]


@dataclass(frozen=True)
class TestOutcome:
    data_source: DataSource
    result: ConnectivityResult


def tenant_of(principal: Principal) -> uuid.UUID:
    return uuid.UUID(principal.tenant_id)


def ensure_enabled(data_source: DataSource) -> None:
    if data_source.status == STATUS_DISABLED:
        raise DataSourceDisabledError()


def connector_for(
    connectors: Mapping[str, CatalogConnector], data_source: DataSource
) -> CatalogConnector:
    connector = connectors.get(data_source.engine)
    if connector is None:
        raise EngineNotSupportedError()
    return connector


async def load_connection_target(secrets: SecretStore, data_source: DataSource) -> ConnectionTarget:
    """Fetch credentials server-side (Section 13.1). Never cached, never returned."""
    ref = data_source_secret_ref(data_source.tenant_id, data_source.id)
    try:
        payload = await secrets.read(ref)
    except SecretStoreError:
        raise SecretStoreUnavailableError() from None
    if payload is None:
        raise SecretNotConfiguredError()
    try:
        secret = ConnectionSecret.from_secret_payload(payload)
    except ValueError:
        raise SecretNotConfiguredError() from None
    return ConnectionTarget(
        engine=data_source.engine,
        database_name=data_source.database_name,
        allowed_schemas=tuple(data_source.allowed_schemas),
        secret=secret,
    )


def audit_view(data_source: DataSource) -> dict[str, Any]:
    """The non-secret fields an audit row may carry."""
    return {
        "name": data_source.name,
        "engine": data_source.engine,
        "host_label": data_source.host_label,
        "database_name": data_source.database_name,
        "allowed_schemas": list(data_source.allowed_schemas),
        "status": data_source.status,
    }


class DataSourceService:
    def __init__(
        self,
        *,
        repository: MetadataRepository,
        secrets: SecretStore,
        connectors: Mapping[str, CatalogConnector],
        egress: EgressPolicy,
        audit: AuditSink,
        vault_mount: str,
    ) -> None:
        self._repository = repository
        self._secrets = secrets
        self._connectors = connectors
        self._egress = egress
        self._audit = audit
        self._vault_mount = vault_mount

    async def create(
        self, principal: Principal, new: NewDataSource, *, ip_address: str | None
    ) -> DataSource:
        if new.engine not in SUPPORTED_ENGINES or new.engine not in self._connectors:
            raise EngineNotSupportedError()
        tenant_id = tenant_of(principal)
        data_source_id = uuid.uuid4()
        data_source = await self._repository.add_data_source(
            DataSource(
                id=data_source_id,
                tenant_id=tenant_id,
                name=new.name,
                engine=new.engine,
                host_label=new.host_label,
                database_name=new.database_name,
                allowed_schemas=list(new.allowed_schemas),
                secret_ref=vault_kv2_path(
                    self._vault_mount, data_source_secret_ref(tenant_id, data_source_id)
                ),
                created_by=uuid.UUID(principal.user_id),
            )
        )
        await self._repository.commit()
        await self._audit.record(
            AuditRecord(
                tenant_id=tenant_id,
                actor_user_id=uuid.UUID(principal.user_id),
                event_type=EVENT_CONNECTION_CREATED,
                resource_type=RESOURCE_TYPE,
                resource_id=str(data_source.id),
                after_state=audit_view(data_source),
                ip_address=ip_address,
            )
        )
        return data_source

    async def get(self, principal: Principal, data_source_id: uuid.UUID) -> DataSource:
        data_source = await self._repository.get_data_source(tenant_of(principal), data_source_id)
        if data_source is None:
            raise NotFoundError()
        return data_source

    async def list(
        self, principal: Principal, *, limit: int, cursor: uuid.UUID | None
    ) -> tuple[list[DataSource], uuid.UUID | None]:
        return await self._repository.list_data_sources(
            tenant_of(principal), limit=limit, cursor=cursor
        )

    async def set_secret(
        self,
        principal: Principal,
        data_source_id: uuid.UUID,
        secret: ConnectionSecret,
        *,
        ip_address: str | None,
    ) -> DataSource:
        """Write credentials to Vault (Section 9: `data:manage`, step-up)."""
        data_source = await self.get(principal, data_source_id)
        ensure_enabled(data_source)
        if not self._egress.permits_literal(secret.host):
            raise DestinationNotAllowedError()
        before = {"status": data_source.status}
        try:
            await self._secrets.write(
                data_source_secret_ref(data_source.tenant_id, data_source.id),
                secret.to_secret_payload(),
            )
        except SecretStoreError:
            raise SecretStoreUnavailableError() from None
        data_source = await self._repository.set_status(data_source, status=STATUS_PENDING)
        await self._repository.commit()
        await self._audit.record(
            AuditRecord(
                tenant_id=data_source.tenant_id,
                actor_user_id=uuid.UUID(principal.user_id),
                event_type=EVENT_CONNECTION_SECRET_ROTATED,
                resource_type=RESOURCE_TYPE,
                resource_id=str(data_source.id),
                before_state=before,
                after_state={"status": data_source.status, "credentials": "updated"},
                ip_address=ip_address,
            )
        )
        return data_source

    async def test(self, principal: Principal, data_source_id: uuid.UUID) -> TestOutcome:
        """Bounded connectivity check returning sanitized diagnostics only (Section 13.1)."""
        tenant_id = tenant_of(principal)
        data_source = await self.get(principal, data_source_id)
        ensure_enabled(data_source)
        connector = connector_for(self._connectors, data_source)
        target = await load_connection_target(self._secrets, data_source)
        # Do not hold a database transaction open across the customer round trip.
        await self._repository.commit()

        result = await connector.test(target)

        locked = await self._repository.get_data_source(tenant_id, data_source_id, for_update=True)
        if locked is None:
            raise NotFoundError()
        if locked.status != STATUS_DISABLED:
            locked = await self._repository.set_status(
                locked, status=STATUS_ACTIVE if result.ok else STATUS_ERROR
            )
        await self._repository.commit()
        return TestOutcome(data_source=locked, result=result)
