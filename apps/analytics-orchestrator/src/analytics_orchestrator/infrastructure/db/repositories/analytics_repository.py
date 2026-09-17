"""Data access for the `analytics` schema. Every query filters on `tenant_id` (Section 19); the
session is additionally RLS-bound. `run_events` is insert-only."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from analytics_orchestrator.infrastructure.db.models import Conversation, Message, Run, RunEvent


class AnalyticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    # --- conversations & messages -------------------------------------------------------------

    async def add_conversation(self, conversation: Conversation) -> Conversation:
        self._session.add(conversation)
        await self._session.flush()
        await self._session.refresh(conversation)
        return conversation

    async def get_conversation(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Conversation | None:
        result = await self._session.execute(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id, Conversation.id == conversation_id
            )
        )
        return result.scalar_one_or_none()

    async def get_conversation_tenant_id(self, conversation_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(
            select(Conversation.tenant_id).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def add_message(self, message: Message) -> Message:
        self._session.add(message)
        await self._session.flush()
        return message

    async def has_assistant_message(self, tenant_id: uuid.UUID, run_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(Message.id)
            .where(
                Message.tenant_id == tenant_id,
                Message.run_id == run_id,
                Message.role == "assistant",
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def get_user_message_for_run(
        self, tenant_id: uuid.UUID, run_id: uuid.UUID
    ) -> Message | None:
        result = await self._session.execute(
            select(Message).where(
                Message.tenant_id == tenant_id, Message.run_id == run_id, Message.role == "user"
            )
        )
        return result.scalar_one_or_none()

    # --- runs ----------------------------------------------------------------------------------

    async def add_run(self, run: Run) -> Run:
        self._session.add(run)
        await self._session.flush()
        await self._session.refresh(run)
        return run

    async def get_run(
        self, tenant_id: uuid.UUID, run_id: uuid.UUID, *, for_update: bool = False
    ) -> Run | None:
        statement = select(Run).where(Run.tenant_id == tenant_id, Run.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement.execution_options(populate_existing=True))
        return result.scalar_one_or_none()

    async def get_run_tenant_id(self, run_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(select(Run.tenant_id).where(Run.id == run_id))
        return result.scalar_one_or_none()

    async def get_run_by_idempotency_key(self, tenant_id: uuid.UUID, key: str) -> Run | None:
        result = await self._session.execute(
            select(Run).where(Run.tenant_id == tenant_id, Run.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def start_run(self, run: Run, flow_state: dict[str, Any]) -> None:
        if run.status == "queued":
            run.status = "running"
            run.started_at = dt.datetime.now(dt.UTC)
        run.flow_state = flow_state
        await self._session.flush()

    async def save_flow_state(
        self, run: Run, flow_state: dict[str, Any], *, current_stage: str | None
    ) -> None:
        run.flow_state = flow_state
        run.current_stage = current_stage
        await self._session.flush()

    async def mark_cancelled(self, run: Run) -> None:
        run.status = "cancelled"
        run.error_code = "CANCELLED"
        await self._session.flush()

    async def finish_run(
        self, run: Run, status: str, error_code: str | None, flow_state: dict[str, Any]
    ) -> None:
        run.status = status
        run.error_code = error_code
        run.flow_state = flow_state
        run.completed_at = dt.datetime.now(dt.UTC)
        await self._session.flush()

    # --- events ---------------------------------------------------------------------------------

    async def append_event(
        self, run: Run, *, stage: str, status: str, message: str, artifact_id: uuid.UUID | None
    ) -> RunEvent:
        """Caller holds the run row lock (`get_run(..., for_update=True)`), so `seq` is gap-free."""
        next_seq = await self._session.scalar(
            select(func.coalesce(func.max(RunEvent.seq), 0) + 1).where(RunEvent.run_id == run.id)
        )
        event = RunEvent(
            run_id=run.id,
            seq=int(next_seq or 1),
            stage=stage,
            status=status,
            message=message,
            artifact_id=artifact_id,
        )
        self._session.add(event)
        await self._session.flush()
        await self._session.refresh(event)
        return event

    async def list_events(self, run_id: uuid.UUID, *, after_seq: int) -> list[RunEvent]:
        result = await self._session.execute(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
            .order_by(RunEvent.seq)
        )
        return list(result.scalars().all())
