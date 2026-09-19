"""`POST /internal/v1/queries`: the Section 13 pipeline, in order.

    authenticate caller (service JWT, scope=query-gateway:execute)     -> api layer
    authenticate the user (forwarded credential, introspection)          -> api layer
    purpose: calling service may request it; user holds its permission  -> _authorize
    load connection policy (metadata-service, cached; tenant-bound)      -> _load_policy
    parse + AST allow-list + identifier allow-list                       -> SqlValidator
    per-tenant concurrency cap                                           -> ConcurrencyLimiter
    fetch credential (Vault, pointer verified against tenant + source)   -> _credentials
    execute read-only with timeout, row and byte caps                    -> QueryExecutor
    store result handle with TTL                                         -> ResultStore
    audit row -- for every outcome, including rejections                 -> _record

A per-connection grant (Section 7.1 "sql:execute (per-connection grant)") has no storage in
Section 8; ADR 0005 records that gap. Tenant ownership is enforced in both services.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Final, Protocol

from platform_auth import Principal, StepUpRequiredError
from platform_auth.permissions import PERM_CHAT_USE, PERM_SQL_EXECUTE, ROLE_ORG_ADMIN
from platform_secrets import SecretStore, SecretStoreError, vault_kv2_path
from query_gateway.domain.errors import (
    DataSourceNotActiveError,
    DataSourceUnavailableError,
    DomainError,
    EngineNotSupportedError,
    ForbiddenError,
    NotFoundError,
    PurposeNotAllowedError,
    PurposeNotSupportedError,
    QueryExecutionFailedError,
    QueryTimeoutError,
    QueryValidationFailedError,
    ResultStoreUnavailableError,
    SecretStoreUnavailableError,
    SqlGrantRequiredError,
    ValidationFailedError,
)
from query_gateway.domain.policies.sql_validator import SqlValidator, ValidationOutcome
from query_gateway.domain.value_objects.execution import (
    ConnectionCredentials,
    ExecutionError,
    ExecutionFailure,
    ExecutionLimits,
    QueryResult,
)
from query_gateway.domain.value_objects.policy import DataSourcePolicy, Purpose
from query_gateway.infrastructure.connectors.base import QueryExecutor
from query_gateway.infrastructure.db.models import QueryExecution
from query_gateway.infrastructure.db.repositories.query_repository import QueryRepository
from query_gateway.infrastructure.http.metadata_client import PolicyLoader
from query_gateway.infrastructure.storage.base import ResultStore, ResultStoreError, StoredResult

logger = logging.getLogger(__name__)


#: Which user permission each purpose requires.
class ConcurrencyLimiter(Protocol):
    """Section 20's per-tenant cap: a slot for the duration of one query, or a 429."""

    def slot(self, tenant_id: uuid.UUID) -> AbstractAsyncContextManager[None]: ...


PURPOSE_PERMISSION: Final[dict[Purpose, str]] = {
    Purpose.SQL_EDITOR: PERM_SQL_EXECUTE,
    Purpose.ANALYTICS_RUN: PERM_CHAT_USE,
}


@dataclass(frozen=True)
class QueryLimits:
    default_max_rows: int
    max_rows_limit: int
    default_timeout_ms: int
    max_timeout_ms: int
    max_result_bytes: int
    #: SQL-editor reads above this many rows are exports (Section 7.3): step-up.
    export_step_up_rows: int


@dataclass(frozen=True)
class QueryCommand:
    data_source_id: uuid.UUID
    sql: str
    purpose: Purpose
    max_rows: int | None = None
    timeout_ms: int | None = None
    run_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ValidatedQuery:
    query_id: uuid.UUID
    sql: str
    tables: tuple[str, ...]
    sql_hash: str


@dataclass(frozen=True)
class QueryOutcome:
    query_id: uuid.UUID
    result: QueryResult
    stored: StoredResult
    duration_ms: int
    tables: tuple[str, ...]


_FAILURE_ERRORS: Final[dict[ExecutionFailure, type[DomainError]]] = {
    ExecutionFailure.TIMEOUT: QueryTimeoutError,
    ExecutionFailure.DESTINATION_NOT_ALLOWED: DataSourceUnavailableError,
    ExecutionFailure.UNAVAILABLE: DataSourceUnavailableError,
    ExecutionFailure.AUTHENTICATION_FAILED: DataSourceUnavailableError,
    ExecutionFailure.REJECTED_BY_DATABASE: QueryExecutionFailedError,
    ExecutionFailure.QUERY_FAILED: QueryExecutionFailedError,
}


def expected_secret_ref(mount: str, tenant_id: uuid.UUID, data_source_id: uuid.UUID) -> str:
    return vault_kv2_path(mount, f"tenants/{tenant_id}/datasources/{data_source_id}")


class QueryService:
    def __init__(
        self,
        *,
        repository: QueryRepository,
        policies: PolicyLoader,
        secrets: SecretStore,
        executors: Mapping[str, QueryExecutor],
        results: ResultStore,
        limiter: ConcurrencyLimiter,
        validator: SqlValidator,
        limits: QueryLimits,
        purpose_callers: Mapping[str, Sequence[str]],
        vault_mount: str,
    ) -> None:
        self._repository = repository
        self._policies = policies
        self._secrets = secrets
        self._executors = executors
        self._results = results
        self._limiter = limiter
        self._validator = validator
        self._limits = limits
        self._purpose_callers = purpose_callers
        self._vault_mount = vault_mount

    def _authorize(self, principal: Principal, caller: str, purpose: Purpose) -> None:
        permission = PURPOSE_PERMISSION.get(purpose)
        if permission is None:
            raise PurposeNotSupportedError()
        if caller not in self._purpose_callers.get(purpose.value, ()):
            raise PurposeNotAllowedError()
        if not principal.has_permission(permission):
            raise ForbiddenError()

    def _execution_limits(self, command: QueryCommand) -> ExecutionLimits:
        max_rows = command.max_rows or self._limits.default_max_rows
        timeout_ms = command.timeout_ms or self._limits.default_timeout_ms
        if max_rows > self._limits.max_rows_limit or timeout_ms > self._limits.max_timeout_ms:
            raise ValidationFailedError(
                max_rows_limit=self._limits.max_rows_limit,
                max_timeout_ms=self._limits.max_timeout_ms,
            )
        return ExecutionLimits(
            max_rows=max_rows, max_bytes=self._limits.max_result_bytes, timeout_ms=timeout_ms
        )

    async def _record(
        self,
        *,
        query_id: uuid.UUID,
        principal: Principal,
        command: QueryCommand,
        validation: ValidationOutcome | None,
        status: str,
        started: float,
        error_code: str | None = None,
        result: QueryResult | None = None,
        stored: StoredResult | None = None,
    ) -> None:
        raw_hash = hashlib.sha256(command.sql.encode("utf-8")).hexdigest()
        await self._repository.record(
            QueryExecution(
                id=query_id,
                tenant_id=uuid.UUID(principal.tenant_id),
                run_id=command.run_id,
                data_source_id=command.data_source_id,
                requested_by=principal.user_id,
                purpose=command.purpose.value,
                sql_text=command.sql,
                sql_hash=(validation.sql_hash if validation and validation.sql_hash else raw_hash),
                validation_result=validation.to_audit() if validation else {"valid": None},
                status=status,
                row_count=result.row_count if result else None,
                bytes_returned=result.bytes_returned if result else None,
                duration_ms=round((time.perf_counter() - started) * 1000),
                result_handle=stored.handle if stored else None,
                error_code=error_code,
            )
        )
        await self._repository.commit()

    async def _credentials(self, policy: DataSourcePolicy) -> ConnectionCredentials:
        expected = expected_secret_ref(self._vault_mount, policy.tenant_id, policy.data_source_id)
        if policy.secret_ref != expected:
            # A pointer outside this tenant's own path is never followed.
            logger.error(
                "secret_ref mismatch",
                extra={"context": {"data_source_id": str(policy.data_source_id)}},
            )
            raise DataSourceUnavailableError()
        ref = expected.split("/data/", 1)[1]
        try:
            payload = await self._secrets.read(ref)
        except SecretStoreError:
            raise SecretStoreUnavailableError() from None
        if payload is None:
            raise DataSourceNotActiveError()
        try:
            return ConnectionCredentials.from_secret_payload(payload)
        except ValueError:
            raise DataSourceUnavailableError() from None

    async def _prepare(
        self, principal: Principal, caller: str, command: QueryCommand
    ) -> tuple[DataSourcePolicy, QueryExecutor]:
        self._authorize(principal, caller, command.purpose)
        tenant_id = uuid.UUID(principal.tenant_id)
        policy = await self._policies.load(tenant_id, command.data_source_id)
        if policy.tenant_id != tenant_id:
            raise NotFoundError()
        executor = self._executors.get(policy.engine)
        if executor is None:
            raise EngineNotSupportedError()
        if policy.status != "active":
            raise DataSourceNotActiveError()
        # Section 7.1: `sql:execute` "(per-connection grant)" for everyone but org_admin.
        if (
            command.purpose is Purpose.SQL_EDITOR
            and not _grant_exempt(principal)
            and not await self._policies.has_sql_grant(
                tenant_id, policy.data_source_id, uuid.UUID(principal.user_id)
            )
        ):
            raise SqlGrantRequiredError()
        return policy, executor

    async def validate(
        self, principal: Principal, caller: str, command: QueryCommand
    ) -> ValidatedQuery:
        """Section 13 authorization and validation without execution; audited either way."""
        policy, _executor = await self._prepare(principal, caller, command)
        query_id = uuid.uuid4()
        started = time.perf_counter()
        validation = self._validator.validate(command.sql, policy, purpose=command.purpose)
        if not validation.ok or validation.sql is None:
            await self._record(
                query_id=query_id,
                principal=principal,
                command=command,
                validation=validation,
                status="rejected",
                started=started,
                error_code=QueryValidationFailedError.code,
            )
            details: dict[str, Any] = {"query_id": str(query_id), "reason": validation.reason}
            if validation.detail:
                details["detail"] = validation.detail
            raise QueryValidationFailedError(validation.message, **details)
        await self._record(
            query_id=query_id,
            principal=principal,
            command=command,
            validation=validation,
            status="validated",
            started=started,
        )
        return ValidatedQuery(
            query_id=query_id,
            sql=validation.sql,
            tables=validation.tables,
            sql_hash=validation.sql_hash or "",
        )

    async def execute(
        self, principal: Principal, caller: str, command: QueryCommand
    ) -> QueryOutcome:
        limits = self._execution_limits(command)
        if (
            command.purpose is Purpose.SQL_EDITOR
            and limits.max_rows > self._limits.export_step_up_rows
            and not principal.step_up_is_fresh()
        ):
            raise StepUpRequiredError.for_principal(principal)
        policy, executor = await self._prepare(principal, caller, command)
        tenant_id = uuid.UUID(principal.tenant_id)

        query_id = uuid.uuid4()
        started = time.perf_counter()
        validation = self._validator.validate(command.sql, policy, purpose=command.purpose)
        if not validation.ok or validation.sql is None:
            await self._record(
                query_id=query_id,
                principal=principal,
                command=command,
                validation=validation,
                status="rejected",
                started=started,
                error_code=QueryValidationFailedError.code,
            )
            details: dict[str, Any] = {"query_id": str(query_id), "reason": validation.reason}
            if validation.detail:
                details["detail"] = validation.detail
            raise QueryValidationFailedError(validation.message, **details)

        async with self._limiter.slot(tenant_id):
            try:
                credentials = await self._credentials(policy)
                result = await executor.execute(
                    pool_key=str(policy.data_source_id),
                    database_name=policy.database_name,
                    credentials=credentials,
                    sql=validation.sql,
                    limits=limits,
                )
            except ExecutionError as error:
                status = "timeout" if error.failure is ExecutionFailure.TIMEOUT else "failed"
                api_error = _FAILURE_ERRORS[error.failure]
                await self._record(
                    query_id=query_id,
                    principal=principal,
                    command=command,
                    validation=validation,
                    status=status,
                    started=started,
                    error_code=error.failure.value,
                )
                extra: dict[str, Any] = {"query_id": str(query_id)}
                if error.sqlstate_class and api_error is QueryExecutionFailedError:
                    extra["sqlstate_class"] = error.sqlstate_class
                raise api_error(**extra) from None
            except DomainError as error:
                await self._record(
                    query_id=query_id,
                    principal=principal,
                    command=command,
                    validation=validation,
                    status="failed",
                    started=started,
                    error_code=error.code,
                )
                raise

            payload = json.dumps(
                {
                    "query_id": str(query_id),
                    "columns": [{"name": c.name, "type": c.type} for c in result.columns],
                    "rows": result.rows,
                    "truncated": result.truncated,
                    "truncation_reason": result.truncation_reason,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            try:
                stored = await self._results.put(
                    tenant_id=tenant_id, query_id=query_id, payload=payload
                )
            except ResultStoreError:
                await self._record(
                    query_id=query_id,
                    principal=principal,
                    command=command,
                    validation=validation,
                    status="failed",
                    started=started,
                    error_code=ResultStoreUnavailableError.code,
                )
                raise ResultStoreUnavailableError(query_id=str(query_id)) from None

        duration_ms = round((time.perf_counter() - started) * 1000)
        await self._record(
            query_id=query_id,
            principal=principal,
            command=command,
            validation=validation,
            status="succeeded",
            started=started,
            result=result,
            stored=stored,
        )
        return QueryOutcome(
            query_id=query_id,
            result=result,
            stored=stored,
            duration_ms=duration_ms,
            tables=validation.tables,
        )


def _grant_exempt(principal: Principal) -> bool:
    return ROLE_ORG_ADMIN in principal.roles or principal.is_platform_operator
