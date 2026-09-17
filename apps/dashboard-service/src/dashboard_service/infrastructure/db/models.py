"""ORM models for the `dashboard` schema (Section 8.6)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Final

from sqlalchemy import DateTime, ForeignKey, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA: Final = "dashboard"
_NOW = text("now()")
_GEN = text("gen_random_uuid()")


class Base(DeclarativeBase):
    pass


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012 - SQLAlchemy declarative

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    semantic_query: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'")
    )
    validated_sql: Mapped[str] = mapped_column(Text, nullable=False)
    query_result_ref: Mapped[str] = mapped_column(Text, nullable=False)
    result_schema: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    chart_spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    refresh_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("""'{"mode":"manual"}'""")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class Dashboard(Base):
    __tablename__ = "dashboards"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012 - SQLAlchemy declarative

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'private'"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class Tile(Base):
    __tablename__ = "tiles"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012 - SQLAlchemy declarative

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.dashboards.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chart_spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class ShareLink(Base):
    """Section 8.6. Created in Phase A6; its routes are Phase A10's."""

    __tablename__ = "share_links"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012 - SQLAlchemy declarative

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.dashboards.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
