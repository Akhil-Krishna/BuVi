"""Service clients the Flow uses: identity-service (delegated principal), metadata-service (agent
context), query-gateway (validate/execute on behalf of the user), visualization-service
(ChartSpec validation), dashboard-service (artifact store) and semantic-service (approved
definitions). Each call carries this service's
own scoped token and the request id; each failure maps onto a typed port exception."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from analytics_orchestrator.application.services.ports import (
    ActiveSource,
    ArtifactDraft,
    ArtifactRejectedError,
    ChartCheck,
    ContextSnapshot,
    DataSourceUnknownError,
    DelegatedUserDeniedError,
    DependencyUnavailableError,
    QueryCall,
    QueryCapacityError,
    QueryDeniedError,
    QueryExecutionError,
    QueryNotActiveError,
    QueryRejectedError,
    QueryTimedOutError,
    SemanticSnapshot,
    ValidatedSql,
)
from analytics_orchestrator.core.config import (
    SCOPE_ARTIFACTS_WRITE,
    SCOPE_CHART_VALIDATE,
    SCOPE_CONTEXT,
    SCOPE_DIRECTORY,
    SCOPE_QUERY_EXECUTE,
    SCOPE_RESOLVE_PRINCIPAL,
    SCOPE_SEMANTIC_CONTEXT,
)
from analytics_orchestrator.domain.policies.context_policy import field_type_for
from analytics_orchestrator.domain.policies.result_policy import result_stats
from analytics_orchestrator.domain.value_objects.run_state import (
    ContextColumn,
    ContextTable,
    ExecutionSummary,
)
from platform_auth import SERVICE_AUTH_HEADER, Principal, ServiceTokenClient, ServiceTokenError
from platform_contracts import ResultField
from platform_observability import REQUEST_ID_HEADER, request_id_var


class _ServiceClient:
    audience: str

    def __init__(
        self, *, base_url: str, http: httpx.AsyncClient, tokens: ServiceTokenClient
    ) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._tokens = tokens

    async def _request(self, method: str, path: str, scope: str, **kwargs: Any) -> httpx.Response:
        try:
            token = await self._tokens.token_for(self.audience, frozenset({scope}))
        except (ServiceTokenError, httpx.HTTPError, KeyError, ValueError):
            raise DependencyUnavailableError() from None
        headers = {SERVICE_AUTH_HEADER: f"Bearer {token}"}
        if request_id := request_id_var.get():
            headers[REQUEST_ID_HEADER] = request_id
        try:
            response = await self._http.request(
                method, f"{self._base}{path}", headers=headers, **kwargs
            )
        except httpx.TimeoutException:
            raise (
                QueryTimedOutError()
                if self.audience == "query-gateway"
                else DependencyUnavailableError()
            ) from None
        except httpx.HTTPError:
            raise DependencyUnavailableError() from None
        if response.status_code >= 500 and response.status_code not in (502, 504):
            raise DependencyUnavailableError()
        return response

    async def ready(self) -> bool:
        try:
            response = await self._http.get(f"{self._base}/health/live", timeout=2.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200


def _error(response: httpx.Response) -> dict[str, Any]:
    try:
        error = response.json().get("error", {})
    except ValueError:
        return {}
    return error if isinstance(error, dict) else {}


class IdentityClient(_ServiceClient):
    audience = "identity-service"

    async def resolve(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Principal:
        response = await self._request(
            "POST",
            "/internal/v1/principals/resolve",
            SCOPE_RESOLVE_PRINCIPAL,
            json={"tenant_id": str(tenant_id), "user_id": str(user_id)},
        )
        if response.status_code in (401, 403, 404):
            raise DelegatedUserDeniedError()
        if response.status_code != 200:
            raise DependencyUnavailableError()
        try:
            principal = Principal.from_dict(response.json()["principal"])
        except (KeyError, TypeError, ValueError):
            raise DependencyUnavailableError() from None
        if principal.tenant_id != str(tenant_id) or principal.user_id != str(user_id):
            raise DependencyUnavailableError()
        return principal

    async def active_seats(self, tenant_id: uuid.UUID) -> int:
        """Active users in the tenant: the seat count `/billing/usage` reports (Section 23)."""
        response = await self._request(
            "POST",
            "/internal/v1/directory/users",
            SCOPE_DIRECTORY,
            json={"tenant_id": str(tenant_id)},
        )
        if response.status_code != 200:
            raise DependencyUnavailableError()
        try:
            users = response.json()["users"]
            return sum(1 for user in users if user["status"] == "active")
        except (KeyError, TypeError, ValueError):
            raise DependencyUnavailableError() from None


class MetadataClient(_ServiceClient):
    audience = "metadata-service"

    async def active_data_sources(self, tenant_id: uuid.UUID) -> list[ActiveSource]:
        response = await self._request(
            "GET", "/internal/v1/data-sources", SCOPE_CONTEXT, params={"tenant_id": str(tenant_id)}
        )
        if response.status_code != 200:
            raise DependencyUnavailableError()
        try:
            return [
                ActiveSource(id=uuid.UUID(item["id"]), name=str(item["name"]))
                for item in response.json()["items"]
            ]
        except (KeyError, TypeError, ValueError):
            raise DependencyUnavailableError() from None

    async def context(self, tenant_id: uuid.UUID, data_source_id: uuid.UUID) -> ContextSnapshot:
        response = await self._request(
            "GET",
            f"/internal/v1/data-sources/{data_source_id}/context",
            SCOPE_CONTEXT,
            params={"tenant_id": str(tenant_id)},
        )
        if response.status_code == 404:
            raise DataSourceUnknownError()
        if response.status_code != 200:
            raise DependencyUnavailableError()
        try:
            body = response.json()
            if body["tenant_id"] != str(tenant_id):
                raise DataSourceUnknownError()
            return ContextSnapshot(
                status=str(body["status"]),
                engine=str(body.get("engine") or "postgres"),
                tables=[
                    ContextTable(
                        id=str(t["id"]) if t.get("id") else None,
                        schema_name=t["schema_name"],
                        table_name=t["table_name"],
                        description=(t.get("description") or None) and str(t["description"])[:200],
                        row_count_estimate=t.get("row_count_estimate"),
                        columns=[
                            ContextColumn(
                                id=str(c["id"]) if c.get("id") else None,
                                name=c["column_name"],
                                data_type=c["data_type"],
                                description=(c.get("description") or None)
                                and str(c["description"])[:200],
                            )
                            for c in t["columns"][:60]
                        ],
                    )
                    for t in body["tables"]
                ],
            )
        except (KeyError, TypeError, ValueError):
            raise DependencyUnavailableError() from None


class QueryGatewayClient(_ServiceClient):
    audience = "query-gateway"

    @staticmethod
    def _body(call: QueryCall) -> dict[str, Any]:
        return {
            "database_id": str(call.data_source_id),
            "sql": call.sql,
            "purpose": "analytics_run",
            "run_id": str(call.run_id),
            "on_behalf_of": {"tenant_id": str(call.tenant_id), "user_id": str(call.user_id)},
        }

    @staticmethod
    def _raise_for(response: httpx.Response) -> None:
        code = _error(response).get("code")
        if response.status_code == 422 and code == "QUERY_VALIDATION_FAILED":
            details = _error(response).get("details") or {}
            raise QueryRejectedError(
                str(details.get("reason") or "REJECTED"), details.get("detail")
            )
        if response.status_code in (401, 403, 404):
            raise QueryDeniedError()
        if response.status_code == 409:
            raise QueryNotActiveError()
        if response.status_code == 504 and code == "QUERY_TIMEOUT":
            raise QueryTimedOutError()
        if response.status_code == 429 and code == "QUERY_CONCURRENCY_LIMITED":
            raise QueryCapacityError()
        if response.status_code == 422:
            raise QueryExecutionError()
        raise DependencyUnavailableError()

    async def validate(self, call: QueryCall) -> ValidatedSql:
        response = await self._request(
            "POST", "/internal/v1/queries/validate", SCOPE_QUERY_EXECUTE, json=self._body(call)
        )
        if response.status_code != 200:
            self._raise_for(response)
        body = response.json()
        return ValidatedSql(
            query_id=str(body["query_id"]),
            sql=str(body["sql"]),
            tables=[str(t) for t in body["tables"]],
        )

    async def execute(self, call: QueryCall, *, max_rows: int, timeout_ms: int) -> ExecutionSummary:
        response = await self._request(
            "POST",
            "/internal/v1/queries",
            SCOPE_QUERY_EXECUTE,
            json={**self._body(call), "max_rows": max_rows, "timeout_ms": timeout_ms},
            timeout=httpx.Timeout(timeout_ms / 1000 + 15, connect=3.0),
        )
        if response.status_code != 200:
            self._raise_for(response)
        body = response.json()
        schema = [
            ResultField(field=c["name"], type=field_type_for(c["type"])) for c in body["columns"]
        ]
        # ADR 0009: reduce rows to aggregates here; the rows go no further than this function.
        stats = result_stats(
            schema,
            body.get("rows") or [],
            row_count=int(body["row_count"]),
            truncated=bool(body["truncated"]),
        )
        return ExecutionSummary(
            query_id=str(body["query_id"]),
            result_schema=schema,
            row_count=int(body["row_count"]),
            truncated=bool(body["truncated"]),
            result_handle=str(body["result_handle"]),
            result_expires_at=body["result_expires_at"],
            stats=stats,
        )


class VisualizationClient(_ServiceClient):
    audience = "visualization-service"

    async def check(
        self, chart_spec: dict[str, Any], result_schema: list[dict[str, Any]]
    ) -> ChartCheck:
        response = await self._request(
            "POST",
            "/internal/v1/chart-specs/validate",
            SCOPE_CHART_VALIDATE,
            json={"chart_spec": chart_spec, "result_schema": result_schema},
        )
        if response.status_code != 200:
            raise DependencyUnavailableError()
        try:
            body = response.json()
            return ChartCheck(
                valid=bool(body["valid"]),
                chart_spec=body.get("chart_spec"),
                problems=[str(p) for p in body.get("problems", [])][:20],
            )
        except (KeyError, TypeError, ValueError):
            raise DependencyUnavailableError() from None


class DashboardClient(_ServiceClient):
    audience = "dashboard-service"

    async def store(self, draft: ArtifactDraft) -> None:
        response = await self._request(
            "POST",
            "/internal/v1/artifacts",
            SCOPE_ARTIFACTS_WRITE,
            json={
                "artifact_id": str(draft.artifact_id),
                "tenant_id": str(draft.tenant_id),
                "conversation_id": str(draft.conversation_id),
                "run_id": str(draft.run_id),
                "title": draft.title,
                "summary": draft.summary,
                "semantic_query": draft.semantic_query,
                "source_refs": draft.source_refs,
                "validated_sql": draft.validated_sql,
                "query_result_ref": draft.query_result_ref,
                "result_schema": draft.result_schema,
                "chart_spec": draft.chart_spec,
                "created_by": str(draft.created_by),
            },
        )
        if response.status_code in (200, 201):
            return
        if response.status_code in (409, 422):
            raise ArtifactRejectedError()
        raise DependencyUnavailableError()


class SemanticClient(_ServiceClient):
    audience = "semantic-service"

    async def context(self, tenant_id: uuid.UUID) -> SemanticSnapshot:
        response = await self._request(
            "GET",
            "/internal/v1/semantic-context",
            SCOPE_SEMANTIC_CONTEXT,
            params={"tenant_id": str(tenant_id)},
        )
        if response.status_code != 200:
            raise DependencyUnavailableError()
        try:
            body = response.json()
            if body["tenant_id"] != str(tenant_id):
                raise DependencyUnavailableError()
            return SemanticSnapshot(
                metrics=[dict(m) for m in body["metrics"]],
                dimensions=[dict(d) for d in body["dimensions"]],
            )
        except (KeyError, TypeError, ValueError):
            raise DependencyUnavailableError() from None
