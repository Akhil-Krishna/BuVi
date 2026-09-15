"""Section 6.3 service identity, purpose policy, and the Section 25 triplet on `database_id`."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from query_gateway.tests.conftest import Api, FakeServices, audit_rows

pytestmark = [pytest.mark.integration, pytest.mark.security]

SQL = "SELECT id FROM sales.orders ORDER BY id LIMIT 3"


@pytest.mark.parametrize("role", ["developer", "org_admin"])
async def test_same_tenant_with_sql_execute_is_allowed(
    api: Api, services: FakeServices, tenant: uuid.UUID, role: str
) -> None:
    response = await api.query(
        services.add_user(tenant, {role}), await api.data_source(tenant), SQL
    )
    assert response.status_code == 200, response.text


async def test_cross_tenant_database_id_is_404_like_a_missing_one(
    api: Api, services: FakeServices, tenant: uuid.UUID, other_tenant: uuid.UUID, platform_db: Any
) -> None:
    who = services.add_user(tenant, {"org_admin"})
    foreign = await api.data_source(other_tenant)
    foreign_response = await api.query(who, foreign, SQL)
    missing_response = await api.query(who, uuid.uuid4(), SQL)
    assert foreign_response.status_code == missing_response.status_code == 404
    assert (
        foreign_response.json()["error"]["code"]
        == missing_response.json()["error"]["code"]
        == "NOT_FOUND"
    )
    # The policy lookup carried the caller's tenant, not the resource's.
    assert services.policy_requests[0].url.params["tenant_id"] == str(tenant)
    assert await audit_rows(platform_db, other_tenant) == []


@pytest.mark.parametrize("role", ["client", "auditor", "billing_admin"])
async def test_sql_editor_without_sql_execute_is_403(
    api: Api, services: FakeServices, tenant: uuid.UUID, role: str
) -> None:
    response = await api.query(
        services.add_user(tenant, {role}), await api.data_source(tenant), SQL
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


async def test_analytics_run_without_chat_use_is_403(
    api: Api, services: FakeServices, tenant: uuid.UUID
) -> None:
    orchestrator = api.service_header(subject="analytics-orchestrator")
    response = await api.query(
        services.add_user(tenant, {"billing_admin"}),
        await api.data_source(tenant),
        SQL,
        purpose="analytics_run",
        service=orchestrator,
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("service", "status", "code"),
    [
        ({}, 401, "AUTHENTICATION_REQUIRED"),
        ({"X-Service-Authorization": "Bearer forged"}, 401, "AUTHENTICATION_REQUIRED"),
        ("wrong-audience", 401, "AUTHENTICATION_REQUIRED"),
        ("wrong-scope", 403, "FORBIDDEN"),
    ],
    ids=["no-token", "forged", "wrong-audience", "wrong-scope"],
)
async def test_calling_service_must_hold_query_gateway_execute(
    api: Api, services: FakeServices, tenant: uuid.UUID, service: Any, status: int, code: str
) -> None:
    if service == "wrong-audience":
        service = api.service_header(audience="metadata-service")
    elif service == "wrong-scope":
        service = api.service_header(scope="metadata-service:query-policy")
    response = await api.query(
        services.add_user(tenant, {"developer"}),
        await api.data_source(tenant),
        SQL,
        service=service,
    )
    assert response.status_code == status and response.json()["error"]["code"] == code


async def test_a_user_credential_is_required_even_with_a_valid_service_token(
    api: Api, services: FakeServices, tenant: uuid.UUID
) -> None:
    response = await api.query(None, await api.data_source(tenant), SQL)
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("subject", "purpose", "status", "code"),
    [
        ("analytics-orchestrator", "sql_editor", 403, "PURPOSE_NOT_ALLOWED"),
        ("api-gateway", "analytics_run", 403, "PURPOSE_NOT_ALLOWED"),
        ("metadata-service", "sql_editor", 403, "PURPOSE_NOT_ALLOWED"),
        ("api-gateway", "export", 422, "PURPOSE_NOT_SUPPORTED"),
    ],
)
async def test_purpose_is_bound_to_the_calling_service(
    api: Api,
    services: FakeServices,
    tenant: uuid.UUID,
    subject: str,
    purpose: str,
    status: int,
    code: str,
) -> None:
    who = services.add_user(tenant, {"org_admin"})
    response = await api.query(
        who,
        await api.data_source(tenant),
        SQL,
        purpose=purpose,
        service=api.service_header(subject=subject),
    )
    assert response.status_code == status and response.json()["error"]["code"] == code


async def test_request_body_cannot_smuggle_identity_fields(
    api: Api, services: FakeServices, tenant: uuid.UUID
) -> None:
    who = services.add_user(tenant, {"developer"})
    source = await api.data_source(tenant)
    for smuggled in (
        {"tenant_id": str(uuid.uuid4())},
        {"user_id": "someone"},
        {"sql_override": "x"},
    ):
        response = await api.query(who, source, SQL, **smuggled)
        assert response.status_code == 422, smuggled
