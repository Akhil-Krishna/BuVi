"""ORM models for the `semantic` schema (Section 8.3)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Final

from sqlalchemy import Boolean, DateTime, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA: Final = "semantic"
_NOW = text("now()")
_GEN = text("gen_random_uuid()")


class Base(DeclarativeBase):
    pass


class Metric(Base):
    __tablename__ = "metrics"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012 - SQLAlchemy declarative

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    default_grain: Mapped[str | None] = mapped_column(Text)
    base_table_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    synonyms: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class Dimension(Base):
    __tablename__ = "dimensions"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012 - SQLAlchemy declarative

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    column_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    synonyms: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class JoinRule(Base):
    """Section 8.3. Modeled in A7; no route or Flow use until multi-table metrics."""

    __tablename__ = "join_rules"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012 - SQLAlchemy declarative

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    left_table_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    right_table_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    on_expression: Mapped[str] = mapped_column(Text, nullable=False)
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
