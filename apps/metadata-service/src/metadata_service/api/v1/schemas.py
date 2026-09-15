"""Request and response models for the `/api/v1` surface.

Request models forbid unknown fields: a client that posts `password` or `secret_ref`
to the wrong endpoint gets a 422, and nothing it sent is stored. Validation errors
report field locations and types only, never the input (platform_observability).

No response model has a field for a credential, a host, a user, a DSN, or a Vault
path -- the secret cannot be serialized by accident.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from metadata_service.domain.value_objects.connection import (
    MAX_ALLOWED_SCHEMAS,
    SslMode,
    validate_database_name,
    validate_host,
    validate_host_label,
    validate_schema_name,
    validate_username,
)

Engine = Literal["postgres", "mysql", "snowflake", "bigquery", "redshift"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    service: str
    checks: dict[str, str] = Field(default_factory=dict)


# --- Data sources -------------------------------------------------------------------


class DataSourceCreateRequest(BaseModel):
    """Non-secret connection metadata. Credentials go to `POST .../secret`."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    engine: Engine
    host_label: Annotated[str, Field(min_length=1, max_length=200)]
    database_name: Annotated[str, Field(min_length=1, max_length=63)]
    allowed_schemas: Annotated[list[str], Field(min_length=1, max_length=MAX_ALLOWED_SCHEMAS)]

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or any(ord(ch) < 32 for ch in stripped):
            raise ValueError("invalid name")
        return stripped

    @field_validator("host_label")
    @classmethod
    def _host_label(cls, value: str) -> str:
        return validate_host_label(value)

    @field_validator("database_name")
    @classmethod
    def _database_name(cls, value: str) -> str:
        return validate_database_name(value)

    @field_validator("allowed_schemas")
    @classmethod
    def _schemas(cls, value: list[str]) -> list[str]:
        names = [validate_schema_name(name) for name in value]
        if len(set(names)) != len(names):
            raise ValueError("duplicate schema")
        return names


class ConnectionSecretRequest(BaseModel):
    """Credentials, written to Vault and never returned (Section 13.1)."""

    model_config = ConfigDict(extra="forbid")

    host: Annotated[str, Field(min_length=1, max_length=253)]
    port: Annotated[int, Field(ge=1, le=65535)] = 5432
    username: Annotated[str, Field(min_length=1, max_length=63)]
    password: Annotated[SecretStr, Field(min_length=1, max_length=1024)]
    sslmode: SslMode = "require"

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        return validate_host(value)

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        return validate_username(value)


class DataSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    engine: str
    host_label: str
    database_name: str
    allowed_schemas: list[str]
    capabilities: dict[str, Any]
    status: str
    last_sync_at: dt.datetime | None
    created_by: uuid.UUID
    created_at: dt.datetime
    updated_at: dt.datetime


class DataSourceListResponse(BaseModel):
    items: list[DataSourceResponse]
    next_cursor: str | None


class ConnectivityTestResponse(BaseModel):
    """Sanitized: a stable code and a fixed message, never driver text (Section 13.1)."""

    data_source_id: uuid.UUID
    ok: bool
    code: str
    message: str
    tables_discovered: int | None
    latency_ms: int
    status: str


class SyncResponse(BaseModel):
    """The `metadata.sync.completed` payload (Section 18.1), returned synchronously."""

    data_source_id: uuid.UUID
    ok: bool
    code: str
    message: str
    status: str
    tables_synced: int
    columns_synced: int
    relationships_synced: int
    snapshot_id: uuid.UUID | None
    synced_at: dt.datetime


# --- Catalog -------------------------------------------------------------------------


class TableSummaryResponse(BaseModel):
    id: uuid.UUID
    data_source_id: uuid.UUID
    schema_name: str
    table_name: str
    description: str | None
    row_count_estimate: int | None
    is_visible_to_agent: bool
    column_count: int


class TableListResponse(BaseModel):
    items: list[TableSummaryResponse]
    next_cursor: str | None


class ColumnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    column_name: str
    data_type: str
    is_pii: bool
    description: str | None


class ColumnRefResponse(BaseModel):
    column_id: uuid.UUID
    table_id: uuid.UUID
    schema_name: str
    table_name: str
    column_name: str


class RelationshipResponse(BaseModel):
    id: uuid.UUID
    relationship_type: str
    from_column: ColumnRefResponse
    to_column: ColumnRefResponse


class TableDetailResponse(TableSummaryResponse):
    columns: list[ColumnResponse]
    relationships: list[RelationshipResponse]


# --- Internal: query policy for query-gateway (Section 13) ---------------------------------


class QueryPolicyColumn(BaseModel):
    column_name: str
    data_type: str
    is_pii: bool


class QueryPolicyTable(BaseModel):
    schema_name: str
    table_name: str
    is_visible_to_agent: bool
    columns: list[QueryPolicyColumn]

    @classmethod
    def from_catalog(cls, table: Any, columns: list[Any]) -> QueryPolicyTable:
        return cls(
            schema_name=table.schema_name,
            table_name=table.table_name,
            is_visible_to_agent=table.is_visible_to_agent,
            columns=[
                QueryPolicyColumn(column_name=c.column_name, data_type=c.data_type, is_pii=c.is_pii)
                for c in columns
            ],
        )


class QueryPolicyResponse(BaseModel):
    """Everything query-gateway needs to validate and route a query. A pointer, never a secret."""

    data_source_id: uuid.UUID
    tenant_id: uuid.UUID
    engine: str
    database_name: str
    allowed_schemas: list[str]
    status: str
    #: Vault path of the credential, read by query-gateway itself (Section 13.1).
    secret_ref: str
    last_sync_at: dt.datetime | None
    tables: list[QueryPolicyTable]
