"""Internal service-to-service API (Section 6.3). Never routed by api-gateway.

* `POST /internal/v1/oauth/token` -- client-credentials grant (form-encoded, RFC 6749 4.4).
* `GET  /internal/v1/jwks.json`   -- public keys for verifying service tokens.
* `POST /internal/v1/introspect`  -- resolve a session token or API key to a Principal;
  requires a service token with `identity-service:introspect`.
* `POST /internal/v1/principals/resolve` -- a user's *current* principal (status, roles), for a
  service acting on that user's behalf without a live session (Section 13, ADR 0006); requires
  `identity-service:resolve-principal`. The result is never step-up capable.
* `POST /internal/v1/audit-events` -- append an audit event on behalf of another service
  (e.g. metadata-service `connection.*`, Sections 7.3 and 22); requires
  `identity-service:audit` and an event type inside the client's registered namespace.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any, Literal
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, model_validator

from identity_service.application.services.audit_service import AuditService
from identity_service.application.services.service_token_service import ServiceTokenService
from identity_service.core.config import (
    SCOPE_AUDIT_WRITE,
    SCOPE_INTROSPECT,
    SCOPE_RESOLVE_PRINCIPAL,
)
from identity_service.dependencies import (
    authenticate_credentials,
    get_app_settings,
    get_service_token_issuer,
    get_session_factory,
)
from identity_service.domain.errors import (
    AuditEventNotAllowedError,
    NotFoundError,
    UserNotActiveError,
    ValidationFailedError,
)
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)
from identity_service.infrastructure.db.session import tenant_scope
from platform_auth import Principal, ServiceIdentity, permissions_for_roles, require_service_scope

router = APIRouter(prefix="/internal/v1", tags=["internal"])

_MAX_FORM_BYTES = 4096
_MAX_AUDIT_STATE_BYTES = 16_384


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int
    scope: str


class IntrospectRequest(BaseModel):
    session_token: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    api_key: Annotated[str, Field(min_length=1, max_length=512)] | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> IntrospectRequest:
        if (self.session_token is None) == (self.api_key is None):
            raise ValueError("provide exactly one of session_token or api_key")
        return self


class IntrospectResponse(BaseModel):
    principal: dict[str, Any]


class ResolvePrincipalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID
    user_id: uuid.UUID


class AuditEventRequest(BaseModel):
    """One `identity.audit_events` row, recorded on behalf of a calling service."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID
    actor_user_id: uuid.UUID | None = None
    actor_type: Literal["user", "service_account", "system"] = "user"
    event_type: Annotated[str, Field(pattern=r"^[a-z][a-z_]*(\.[a-z][a-z_]*)+$", max_length=100)]
    resource_type: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    resource_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    ip_address: IPvAnyAddress | None = None


@router.post(
    "/oauth/token",
    response_model=TokenResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/x-www-form-urlencoded": {
                    "schema": {
                        "type": "object",
                        "required": ["grant_type", "client_id", "client_secret", "audience"],
                        "properties": {
                            "grant_type": {"type": "string", "enum": ["client_credentials"]},
                            "client_id": {"type": "string"},
                            "client_secret": {"type": "string"},
                            "audience": {"type": "string"},
                            "scope": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
async def token(request: Request) -> TokenResponse:
    body = await request.body()
    if len(body) > _MAX_FORM_BYTES:
        raise ValidationFailedError()
    form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items() if v}
    service = ServiceTokenService(
        issuer=get_service_token_issuer(request), settings=get_app_settings(request)
    )
    issued = service.exchange(
        grant_type=form.get("grant_type", ""),
        client_id=form.get("client_id", ""),
        client_secret=form.get("client_secret", ""),
        audience=form.get("audience", ""),
        requested_scopes=frozenset(form.get("scope", "").split()),
    )
    return TokenResponse(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        scope=" ".join(sorted(issued.scopes)),
    )


@router.get("/jwks.json")
async def jwks(request: Request) -> dict[str, Any]:
    return get_service_token_issuer(request).jwks()


@router.post("/introspect", response_model=IntrospectResponse)
async def introspect(
    request: Request,
    payload: IntrospectRequest,
    _service: Annotated[ServiceIdentity, Depends(require_service_scope(SCOPE_INTROSPECT))],
) -> IntrospectResponse:
    principal = await authenticate_credentials(
        request, session_token=payload.session_token, api_key=payload.api_key
    )
    return IntrospectResponse(principal=principal.to_dict())


@router.post("/audit-events", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def record_audit_event(
    request: Request,
    payload: AuditEventRequest,
    service: Annotated[ServiceIdentity, Depends(require_service_scope(SCOPE_AUDIT_WRITE))],
) -> Response:
    """Append an audit event for another service (Sections 7.3, 22).

    `identity.audit_events` has one owner, so services that perform audited operations
    (metadata-service: connection creation and credential changes) record them here
    instead of writing another service's table (Section 37). The client's registered
    namespace bounds what it may write, so a compromised caller cannot forge, say, a
    `user.role_changed` row. Secret-shaped keys are redacted like every other row.
    """
    client = get_app_settings(request).service_clients.get(service.subject)
    prefixes = tuple(client.audit_event_prefixes) if client else ()
    if not prefixes or not payload.event_type.startswith(prefixes):
        raise AuditEventNotAllowedError()
    states = json.dumps([payload.before_state, payload.after_state], default=str)
    if len(states.encode("utf-8")) > _MAX_AUDIT_STATE_BYTES:
        raise ValidationFailedError()

    async with tenant_scope(get_session_factory(request), payload.tenant_id) as db:
        await AuditService(IdentityRepository(db)).record(
            event_type=payload.event_type,
            tenant_id=payload.tenant_id,
            actor_user_id=payload.actor_user_id,
            actor_type=payload.actor_type,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            before_state=payload.before_state,
            after_state=payload.after_state,
            ip_address=str(payload.ip_address) if payload.ip_address else None,
        )
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/principals/resolve", response_model=IntrospectResponse)
async def resolve_principal_for_service(
    request: Request,
    payload: ResolvePrincipalRequest,
    _service: Annotated[ServiceIdentity, Depends(require_service_scope(SCOPE_RESOLVE_PRINCIPAL))],
) -> IntrospectResponse:
    """The user's principal as it is *now* -- a queued run must not outlive a revoked role.

    Resolved inside the given tenant (RLS-bound), so a user id from another tenant is `404`.
    `auth_method` is `service_jwt` and MFA is never marked verified: delegated work can never
    satisfy a Section 7.3 step-up check.
    """
    async with tenant_scope(get_session_factory(request), payload.tenant_id) as db:
        repository = IdentityRepository(db)
        user = await repository.get_user(payload.tenant_id, payload.user_id)
        roles = await repository.get_user_role_keys(user.id) if user is not None else frozenset()
        await db.commit()
    if user is None:
        raise NotFoundError()
    if user.status != "active":
        raise UserNotActiveError()
    principal = Principal(
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
        permissions=permissions_for_roles(roles),
        auth_method="service_jwt",
        mfa_verified=False,
        roles=frozenset(roles),
    )
    return IntrospectResponse(principal=principal.to_dict())
