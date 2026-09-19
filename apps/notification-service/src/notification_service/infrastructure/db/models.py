"""SQLAlchemy mapping of the `notification` schema (Section 8.8), transcribed from the DDL."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "notification"
_NOW = text("now()")
_GEN = text("gen_random_uuid()")


class Base(DeclarativeBase):
    """Declarative base scoped to the `notification` schema."""


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("channel IN ('in_app','email','webhook')", name="ck_notifications_channel"),
        CheckConstraint(
            "status IN ('queued','sent','failed','read')", name="ck_notifications_status"
        ),
        UniqueConstraint(
            "event_key", "user_id", "channel", name="notifications_event_key_user_id_channel_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    template_key: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    event_key: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"
    __table_args__ = (
        CheckConstraint("status IN ('active','disabled')", name="ck_webhook_subscriptions_status"),
        Index("idx_webhook_subscriptions_tenant", "tenant_id", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    event_types: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    #: Vault path of the HMAC signing secret (Section 8.8); the secret itself is never stored here.
    signing_secret_ref: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
