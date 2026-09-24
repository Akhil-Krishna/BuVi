"""analytics.run_events.status gains 'cancelled' (Section 11; Track B prerequisite, ADR 0017)

A cancelled run rode the ordinary `run.failed` event, distinguishable from a real failure only by
matching the fixed message string "The run was cancelled." -- fragile, and against this project's
own rule that UI behavior keys off typed data, not message text. Additive: existing rows and the
`started`/`completed`/`failed` values are unaffected.

Revision ID: 0003_run_events_cancelled_status
Revises: 0002_usage_records
Create Date: 2026-09-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_run_events_cancelled_status"
down_revision: str | None = "0002_usage_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "analytics"


def upgrade() -> None:
    op.drop_constraint("ck_run_events_status", "run_events", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_run_events_status",
        "run_events",
        "status IN ('started','completed','failed','cancelled')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint("ck_run_events_status", "run_events", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_run_events_status",
        "run_events",
        "status IN ('started','completed','failed')",
        schema=SCHEMA,
    )
