"""Refusal events cannot be written in the request transaction (regression guard, Phase A10).

A refused request rolls back, so an audit row added to its transaction disappears with it. The
integration test covers one path end to end; these pin the rule for every refusal event and
every future caller: `record` refuses them, and `record_failure` never falls back to it.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest

from identity_service.application.services import audit_service as events
from identity_service.application.services.audit_service import AuditService, AuditWiringError
from identity_service.infrastructure.db.independent import IndependentWrites
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)

pytestmark = pytest.mark.unit

TENANT = uuid.uuid4()


class _Repository:
    def __init__(self) -> None:
        self.added: list[Any] = []

    async def add_audit_event(self, event: Any) -> None:
        self.added.append(event)


class _Independent:
    """Runs the work against its own repository, as a separate transaction would."""

    def __init__(self) -> None:
        self.repository = _Repository()
        self.tenants: list[uuid.UUID | None] = []

    async def run(
        self,
        tenant_id: uuid.UUID | None,
        work: Callable[[IdentityRepository], Awaitable[None]],
    ) -> None:
        self.tenants.append(tenant_id)
        await work(cast(IdentityRepository, self.repository))


@pytest.mark.parametrize("event_type", sorted(events.REFUSAL_EVENTS))
async def test_record_refuses_refusal_events(event_type: str) -> None:
    repository = _Repository()
    audit = AuditService(cast(IdentityRepository, repository))
    with pytest.raises(AuditWiringError):
        await audit.record(event_type=event_type, tenant_id=TENANT)
    assert repository.added == []


async def test_record_failure_without_independent_writes_fails_loudly() -> None:
    repository = _Repository()
    audit = AuditService(cast(IdentityRepository, repository))
    with pytest.raises(AuditWiringError):
        await audit.record_failure(event_type=events.EVENT_LOGIN_FAILED, tenant_id=TENANT)
    assert repository.added == []


@pytest.mark.parametrize("event_type", sorted(events.REFUSAL_EVENTS))
async def test_record_failure_writes_outside_the_request_transaction(event_type: str) -> None:
    request_repository = _Repository()
    independent = _Independent()
    audit = AuditService(
        cast(IdentityRepository, request_repository), cast(IndependentWrites, independent)
    )
    await audit.record_failure(event_type=event_type, tenant_id=TENANT)
    assert request_repository.added == []
    assert [e.event_type for e in independent.repository.added] == [event_type]
    assert independent.tenants == [TENANT]


async def test_ordinary_events_stay_in_the_request_transaction() -> None:
    repository = _Repository()
    audit = AuditService(cast(IdentityRepository, repository))
    await audit.record(event_type=events.EVENT_LOGIN, tenant_id=TENANT)
    assert [e.event_type for e in repository.added] == [events.EVENT_LOGIN]
