"""Request and response models for the `/api/v1` surface (Sections 9, 21).

Pydantic v2 at every API boundary (Section 5). Response models are explicit
rather than serialised ORM rows, so a column added later cannot silently widen
the public contract -- which is how `secret_hash` or `idp_refresh_token_ref`
would otherwise end up in a response.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# --- Error envelope (Section 21) ---------------------------------------------


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """The single shape every 4xx/5xx response takes."""

    error: ErrorBody


# --- Health (Section 28) ------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    checks: dict[str, str] = Field(default_factory=dict)


# --- Auth (Sections 6.1, 9) ---------------------------------------------------


class SessionResponse(BaseModel):
    """`GET /auth/session`: the current principal, roles and tenant."""

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: EmailStr
    display_name: str
    roles: list[str]
    permissions: list[str]
    auth_method: str
    mfa_enabled: bool
    mfa_verified: bool
    step_up_fresh: bool
    session_id: uuid.UUID | None
    expires_at: dt.datetime | None


class LogoutResponse(BaseModel):
    status: Literal["logged_out"] = "logged_out"


# --- MFA (Section 6.6) --------------------------------------------------------


class MfaEnrollResponse(BaseModel):
    """Returned once. The URI embeds the shared secret; do not log it."""

    method: Literal["totp"] = "totp"
    secret: str
    provisioning_uri: str


class MfaVerifyRequest(BaseModel):
    code: Annotated[str, Field(min_length=6, max_length=10, pattern=r"^[0-9]+$")]


class MfaVerifyResponse(BaseModel):
    mfa_enabled: bool
    verified_at: dt.datetime
    step_up_expires_at: dt.datetime


# --- Users and invitations (Sections 6.7, 9) ----------------------------------


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    email: EmailStr
    display_name: str
    status: str
    mfa_enabled: bool
    roles: list[str] = Field(default_factory=list)
    last_login_at: dt.datetime | None = None
    created_at: dt.datetime


class UserListResponse(BaseModel):
    items: list[UserResponse]
    next_cursor: str | None = None


class InvitationCreateRequest(BaseModel):
    email: EmailStr
    role_key: Annotated[str, Field(min_length=1, max_length=64)]


class InvitationResponse(BaseModel):
    """Never carries the raw token -- that exists only in the invitee's inbox."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    email: EmailStr
    role_key: str
    status: str
    expires_at: dt.datetime
    created_at: dt.datetime


class InvitationAcceptRequest(BaseModel):
    token: Annotated[str, Field(min_length=16, max_length=256)]
    idp_subject: Annotated[str, Field(min_length=1, max_length=255)]
    display_name: Annotated[str, Field(max_length=255)] | None = None


class RoleChangeRequest(BaseModel):
    grant: list[str] = Field(default_factory=list)
    revoke: list[str] = Field(default_factory=list)


class RoleChangeResponse(BaseModel):
    user_id: uuid.UUID
    roles: list[str]


class SessionsRevokedResponse(BaseModel):
    sessions_revoked: int


# --- Sessions (Section 6.9) ---------------------------------------------------


class UserSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_label: str | None
    ip_address: str | None
    user_agent: str | None
    created_at: dt.datetime
    last_seen_at: dt.datetime
    expires_at: dt.datetime
    current: bool = False


class UserSessionListResponse(BaseModel):
    items: list[UserSessionResponse]


# --- API keys (Section 6.8) ---------------------------------------------------


class ApiKeyCreateRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    scopes: list[str] = Field(default_factory=list)
    expires_at: dt.datetime | None = None


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    scopes: list[str]
    expires_at: dt.datetime | None
    last_used_at: dt.datetime | None
    created_at: dt.datetime


class ApiKeyCreatedResponse(BaseModel):
    """The only response that ever contains the key (Section 6.8)."""

    api_key: ApiKeyResponse
    secret: str
    warning: str = "Store this key now. It cannot be retrieved again."


class ApiKeyListResponse(BaseModel):
    items: list[ApiKeyResponse]


# --- Audit (Sections 8.1, 9) ---------------------------------------------------


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    actor_type: str
    event_type: str
    resource_type: str | None
    resource_id: str | None
    request_id: str | None
    created_at: dt.datetime


class AuditEventListResponse(BaseModel):
    items: list[AuditEventResponse]
