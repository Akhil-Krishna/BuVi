"""SQLAlchemy mapping of the `query_gateway` schema (Section 8.5), transcribed from the DDL.

`query_executions` is the query audit: append-only at the database-grant level (migration
0001), and RLS-bound. One row per request, including rejected ones -- a rejected query is a
security signal (Sections 13, 22).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "query_gateway"


class Base(DeclarativeBase):
    """Declarative base scoped to the `query_gateway` schema."""


class QueryExecution(Base):
    __tablename__ = "query_executions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('validated','rejected','running','succeeded','failed','timeout')",
            name="ck_query_executions_status",
        ),
        Index("idx_qe_tenant_time", "tenant_id", text("created_at DESC")),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    data_source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: user_id or service principal id.
    requested_by: Mapped[str] = mapped_column(Text, nullable=False)
    #: 'analytics_run' | 'sql_editor' | 'export'
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    sql_hash: Mapped[str] = mapped_column(Text, nullable=False)
    validation_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int | None] = mapped_column(Integer)
    bytes_returned: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    #: Object storage ref, TTL-bound.
    result_handle: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
