"""ORM models for the `mcp` schema (Section 8.7)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Final

from sqlalchemy import DateTime, ForeignKey, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA: Final = "mcp"
_NOW = text("now()")
_GEN = text("gen_random_uuid()")


class Base(DeclarativeBase):
    pass


class Server(Base):
    __tablename__ = "servers"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012 - SQLAlchemy declarative

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_url: Mapped[str] = mapped_column(Text, nullable=False)
    auth_secret_ref: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending_approval'")
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class Tool(Base):
    __tablename__ = "tools"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012 - SQLAlchemy declarative

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    server_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.servers.id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    tool_class: Mapped[str] = mapped_column(Text, nullable=False)
    default_policy: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'deny'"))


class ToolGrant(Base):
    __tablename__ = "tool_grants"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012 - SQLAlchemy declarative

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tools.id", ondelete="CASCADE"), nullable=False
    )
    grantee_role: Mapped[str | None] = mapped_column(Text)
    grantee_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    granted_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    granted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class Invocation(Base):
    """Append-only for the request role (migration 0001)."""

    __tablename__ = "invocations"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012 - SQLAlchemy declarative

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tool_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    invoked_by: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response_status: Mapped[str] = mapped_column(Text, nullable=False)
    response_summary: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
