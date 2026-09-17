"""Registers every catalog route under `/api/v1` (Section 9)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from api_gateway.application.services import idempotency
from api_gateway.application.services.request_pipeline import authorize, read_body
from api_gateway.application.services.run_event_stream import RunEventStream, parse_last_event_id
from api_gateway.domain.catalog import CATALOG, RouteSpec
from api_gateway.domain.errors import NotFoundError, NotImplementedYetError

_PATH_PARAM = re.compile(r"\{([^}]+)\}")
_ERROR_REF = {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}


def _auth_label(route: RouteSpec) -> str:
    if route.public:
        return "public"
    parts = ["session or API key"]
    if route.permission:
        parts.append(route.permission)
    if route.any_permission:
        parts.append(" or ".join(sorted(route.any_permission)))
    if route.role:
        parts.append(f"role {route.role}")
    if route.step_up:
        parts.append("step-up")
    return ", ".join(parts)


def openapi_extra(route: RouteSpec) -> dict[str, Any]:
    parameters: list[dict[str, Any]] = [
        {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
        for name in _PATH_PARAM.findall(route.path)
    ]
    if route.idempotency_mode != "ignore":
        parameters.append(
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": False,
                "description": (
                    "Section 9: retries with the same key replay the first final response"
                    if route.idempotency_mode == "replay"
                    else "Section 9: a completed request is not re-run; its response is not stored"
                ),
                "schema": {"type": "string", "minLength": 1, "maxLength": 255},
            }
        )
    responses: dict[str, Any] = {"429": {"description": "Rate limited", "content": _ERROR_REF}}
    if route.idempotency_mode != "ignore":
        responses["409"] = {
            "description": "Idempotency-Key reused, in progress, or replay unavailable",
            "content": _ERROR_REF,
        }
    if not route.public:
        responses["401"] = {"description": "Authentication required", "content": _ERROR_REF}
        responses["403"] = {"description": "Forbidden", "content": _ERROR_REF}
    if route.is_stub:
        responses["501"] = {
            "description": "Backing service not available yet",
            "content": _ERROR_REF,
        }
    else:
        responses["502"] = {"description": "Upstream unavailable", "content": _ERROR_REF}
        responses["504"] = {"description": "Upstream timeout", "content": _ERROR_REF}
    extra: dict[str, Any] = {
        "parameters": parameters,
        "responses": responses,
        "x-auth": _auth_label(route),
        "x-backend": route.backend,
        "x-rate-tier": route.rate_tier,
    }
    if route.stream:
        extra["responses"] = {
            **responses,
            "200": {
                "description": "AnalyticsRunEvent stream (Section 11)",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            },
        }
        extra["x-stream"] = "server-sent-events"
    if route.is_stub:
        extra["x-available-in-phase"] = route.available_in_phase
    if not route.in_section_9:
        extra["x-beyond-section-9"] = route.decision
    return extra


async def _stream_run_events(request: Request, principal: Any) -> Response:
    """Section 11 SSE bridge: authorized above; the orchestrator enforces the resource tenant."""
    state = request.app.state
    settings = state.settings
    try:
        run_id = uuid.UUID(request.path_params["id"])
    except ValueError:
        raise NotFoundError() from None
    stream = RunEventStream(
        redis=state.redis,
        client=state.run_events,
        tenant_id=principal.tenant_id,
        run_id=run_id,
        last_seq=parse_last_event_id(request.headers.get("last-event-id")),
        heartbeat_seconds=settings.sse_heartbeat_seconds,
        max_seconds=settings.sse_max_stream_seconds,
    )
    await stream.open()
    return StreamingResponse(
        stream.frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _endpoint(route: RouteSpec) -> Callable[[Request], Awaitable[Response]]:
    async def endpoint(request: Request) -> Response:
        state = request.app.state
        authorized = await authorize(
            request,
            route,
            settings=state.settings,
            policy=state.rate_limit_policy,
            limiter=state.rate_limiter,
            identity=state.identity,
        )
        if route.stream:
            return await _stream_run_events(request, authorized.principal)
        if route.is_stub or route.backend is None:
            raise NotImplementedYetError(
                backend=route.backend, available_in_phase=route.available_in_phase
            )
        settings = state.settings
        body = await read_body(request, settings.max_request_body_bytes)
        guard = await idempotency.begin(
            request,
            route,
            authorized.principal,
            body,
            store=state.idempotency,
            lock_seconds=settings.upstream_timeout_seconds + 30,
        )
        if guard.replay is not None:
            return guard.replay
        try:
            response: Response = await state.proxy.forward(
                request,
                backend=route.backend,
                scope=f"{route.backend}:proxy",
                client_ip=authorized.client_ip,
                request_id=request.state.request_id,
                body=body,
            )
        except BaseException:
            await guard.abandon()
            raise
        await guard.finish(
            response,
            ttl_ms=settings.idempotency_ttl_seconds * 1000,
            max_body_bytes=settings.idempotency_max_body_bytes,
        )
        return response

    endpoint.__name__ = route.operation_id
    return endpoint


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    for route in CATALOG:
        router.add_api_route(
            route.path,
            _endpoint(route),
            methods=[route.method],
            summary=route.summary,
            operation_id=route.operation_id,
            tags=[route.backend or "unassigned"],
            openapi_extra=openapi_extra(route),
        )
    return router
