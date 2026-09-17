"""`POST /internal/v1/chart-specs/validate` over HTTP: service auth and the validation result."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from platform_auth import ServiceTokenIssuer, ServiceTokenVerifier
from visualization_service.core.config import Settings
from visualization_service.main import create_app

pytestmark = pytest.mark.integration

ISSUER = ServiceTokenIssuer(issuer="identity-service", private_key_pem=None, key_id="viz-test")
URL = "/internal/v1/chart-specs/validate"
RESULT = [{"field": "month", "type": "temporal"}, {"field": "revenue", "type": "quantitative"}]
SPEC = {
    "type": "bar",
    "encoding": {
        "x": {"field": "month", "type": "temporal"},
        "y": {"field": "revenue", "type": "quantitative"},
    },
}


def _headers(
    scope: str = "visualization-service:validate", audience: str = "visualization-service"
) -> dict[str, str]:
    token = ISSUER.issue(subject="dashboard-service", audience=audience, scopes=frozenset({scope}))
    return {"X-Service-Authorization": f"Bearer {token}"}


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        settings=Settings(environment="test", log_level="WARNING"),
        service_token_verifier=ServiceTokenVerifier(
            issuer="identity-service", audience="visualization-service", keyset=ISSUER.jwks()
        ),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://viz.test") as c,
    ):
        yield c


async def test_valid_spec_is_returned_normalized(client: httpx.AsyncClient) -> None:
    response = await client.post(
        URL, json={"chart_spec": SPEC, "result_schema": RESULT}, headers=_headers()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True and body["problems"] == []
    assert body["chart_spec"]["dataset"] == "artifact-result"


async def test_unknown_field_is_a_rejection_with_problems(client: httpx.AsyncClient) -> None:
    response = await client.post(
        URL,
        json={"chart_spec": {**SPEC, "html": "<b>hi</b>"}, "result_schema": RESULT},
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json() == {
        "valid": False,
        "chart_spec": None,
        "problems": ["html: extra_forbidden"],
    }


async def test_overrides_are_validated_with_the_spec(client: httpx.AsyncClient) -> None:
    ok = await client.post(
        URL,
        json={"chart_spec": SPEC, "result_schema": RESULT, "overrides": {"title": "Q2"}},
        headers=_headers(),
    )
    assert ok.json()["chart_spec"]["options"]["title"] == "Q2"
    bad = await client.post(
        URL,
        json={"chart_spec": SPEC, "result_schema": RESULT, "overrides": {"type": "pie"}},
        headers=_headers(),
    )
    assert bad.json()["valid"] is False


async def test_service_authentication_is_required(client: httpx.AsyncClient) -> None:
    body = {"chart_spec": SPEC, "result_schema": RESULT}
    assert (await client.post(URL, json=body)).status_code == 401
    assert (
        await client.post(URL, json=body, headers=_headers(scope="other:scope"))
    ).status_code == 403
    assert (
        await client.post(URL, json=body, headers=_headers(audience="dashboard-service"))
    ).status_code == 401


async def test_health(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health/live")).json()["status"] == "ok"
    assert (await client.get("/health/ready")).status_code == 200
