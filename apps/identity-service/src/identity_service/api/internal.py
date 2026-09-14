"""Internal service-to-service API (Section 6.3). Never routed by api-gateway.

* `POST /internal/v1/oauth/token` -- client-credentials grant (form-encoded, RFC 6749 4.4).
* `GET  /internal/v1/jwks.json`   -- public keys for verifying service tokens.
* `POST /internal/v1/introspect`  -- resolve a session token or API key to a Principal;
  requires a service token with `identity-service:introspect`.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, model_validator

from identity_service.application.services.service_token_service import ServiceTokenService
from identity_service.core.config import SCOPE_INTROSPECT
from identity_service.dependencies import (
    authenticate_credentials,
    get_app_settings,
    get_service_token_issuer,
)
from identity_service.domain.errors import ValidationFailedError
from platform_auth import ServiceIdentity, require_service_scope

router = APIRouter(prefix="/internal/v1", tags=["internal"])

_MAX_FORM_BYTES = 4096


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
