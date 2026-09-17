"""Conversations, messages and run creation (Sections 9, 18, 20).

`POST /conversations/{id}/messages` creates the user message and a `queued` run in one
transaction, then publishes `analytics.run.requested`. With an `Idempotency-Key`, a retry of the
same request returns the same run; the same key with a different request is a 409.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from analytics_orchestrator.application.services.ports import RunEventPublisher, RunQueue
from analytics_orchestrator.domain.errors import (
    IdempotencyKeyReusedError,
    NotFoundError,
    QueueUnavailableError,
    RunNotCancellableError,
)
from analytics_orchestrator.domain.policies.flow_steps import TERMINAL_STATUSES
from analytics_orchestrator.domain.value_objects.failures import MESSAGES, FailureCode
from analytics_orchestrator.domain.value_objects.run_state import AnalyticsRunState
from analytics_orchestrator.infrastructure.db.models import Conversation, Message, Run
from analytics_orchestrator.infrastructure.db.repositories.analytics_repository import (
    AnalyticsRepository,
)
from platform_auth import Principal
from platform_contracts import AnalyticsRunEvent, RunRequested
from platform_observability import request_id_var

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcceptedRun:
    run: Run
    replayed: bool


class ConversationService:
    def __init__(
        self, *, repository: AnalyticsRepository, queue: RunQueue, events: RunEventPublisher
    ) -> None:
        self._repository = repository
        self._queue = queue
        self._events = events

    async def create_conversation(self, principal: Principal, title: str | None) -> Conversation:
        conversation = await self._repository.add_conversation(
            Conversation(
                tenant_id=uuid.UUID(principal.tenant_id),
                created_by=uuid.UUID(principal.user_id),
                title=title,
            )
        )
        await self._repository.commit()
        return conversation

    async def _replay(
        self,
        principal: Principal,
        key: str,
        conversation_id: uuid.UUID,
        content: str,
        data_source_id: uuid.UUID | None,
    ) -> AcceptedRun | None:
        tenant = uuid.UUID(principal.tenant_id)
        existing = await self._repository.get_run_by_idempotency_key(tenant, key)
        if existing is None:
            return None
        original = await self._repository.get_user_message_for_run(tenant, existing.id)
        stored = (
            AnalyticsRunState.model_validate(existing.flow_state) if existing.flow_state else None
        )
        same = (
            existing.conversation_id == conversation_id
            and str(existing.requested_by) == principal.user_id
            and original is not None
            and original.content == content
            and (stored.data_source_id if stored else None)
            == (str(data_source_id) if data_source_id else None)
        )
        if not same:
            raise IdempotencyKeyReusedError()
        return AcceptedRun(run=existing, replayed=True)

    async def post_message(
        self,
        principal: Principal,
        conversation_id: uuid.UUID,
        *,
        content: str,
        data_source_id: uuid.UUID | None,
        idempotency_key: str | None,
    ) -> AcceptedRun:
        tenant = uuid.UUID(principal.tenant_id)
        if await self._repository.get_conversation(tenant, conversation_id) is None:
            raise NotFoundError()
        if idempotency_key and (
            replay := await self._replay(
                principal, idempotency_key, conversation_id, content, data_source_id
            )
        ):
            return replay

        run_id = uuid.uuid4()
        state = AnalyticsRunState.initial(
            run_id=run_id,
            tenant_id=tenant,
            conversation_id=conversation_id,
            requested_by=uuid.UUID(principal.user_id),
            message=content,
            data_source_id=data_source_id,
        )
        try:
            run = await self._repository.add_run(
                Run(
                    id=run_id,
                    tenant_id=tenant,
                    conversation_id=conversation_id,
                    requested_by=uuid.UUID(principal.user_id),
                    status="queued",
                    flow_state=state.model_dump(mode="json"),
                    idempotency_key=idempotency_key,
                )
            )
            await self._repository.add_message(
                Message(
                    tenant_id=tenant,
                    conversation_id=conversation_id,
                    role="user",
                    content=content,
                    run_id=run_id,
                )
            )
            await self._repository.commit()
        except IntegrityError:
            await self._repository.rollback()
            if idempotency_key and (
                replay := await self._replay(
                    principal, idempotency_key, conversation_id, content, data_source_id
                )
            ):
                return replay
            raise

        try:
            await self._queue.enqueue(
                RunRequested(
                    run_id=run.id,
                    tenant_id=tenant,
                    conversation_id=conversation_id,
                    request_id=request_id_var.get(),
                )
            )
        except Exception:
            logger.error("run enqueue failed", extra={"context": {"run_id": str(run.id)}})
            await self._end_without_execution(run, "failed", FailureCode.RUN_ENQUEUE_FAILED)
            raise QueueUnavailableError() from None
        return AcceptedRun(run=run, replayed=False)

    async def cancel(self, principal: Principal, run_id: uuid.UUID) -> Run:
        tenant = uuid.UUID(principal.tenant_id)
        run = await self._repository.get_run(tenant, run_id, for_update=True)
        if run is None:
            raise NotFoundError()
        if run.status in TERMINAL_STATUSES:
            raise RunNotCancellableError()
        if run.status == "queued":
            await self._end_without_execution(run, "cancelled", FailureCode.CANCELLED)
        else:
            # A running Flow checks status before each step and emits run.failed itself.
            await self._repository.mark_cancelled(run)
            await self._repository.commit()
        return run

    async def _end_without_execution(self, run: Run, status: str, code: FailureCode) -> None:
        run = await self._repository.get_run(run.tenant_id, run.id, for_update=True) or run
        event = await self._repository.append_event(
            run, stage="run", status="failed", message=MESSAGES[code], artifact_id=None
        )
        state = dict(run.flow_state or {})
        state["emitted_events"] = [*state.get("emitted_events", []), "run.failed"]
        await self._repository.finish_run(run, status, code.value, state)
        await self._repository.commit()
        try:
            await self._events.publish(
                AnalyticsRunEvent(
                    run_id=str(run.id),
                    seq=event.seq,
                    stage="run",
                    status="failed",
                    message=event.message,
                    created_at=event.created_at,
                )
            )
        except Exception:
            logger.warning("run event publish failed", extra={"context": {"run_id": str(run.id)}})
