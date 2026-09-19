"""Request and response models for the `/api/v1` surface (Sections 9, 21).

Pydantic v2 at every API boundary (Section 5). Response models are explicit
rather than serialised ORM rows, so a column added later cannot silently widen
the public contract -- which is how `secret_hash` or `idp_refresh_token_ref`
would otherwise end up in a response.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

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
    #: `webauthn` when only a WebAuthn check counts as step-up for this caller (Section 6.6).
    step_up_method: Literal["webauthn"] | None = None
    session_id: uuid.UUID | None
    expires_at: dt.datetime | None


class LogoutResponse(BaseModel):
    status: Literal["logged_out"] = "logged_out"


# --- MFA (Section 6.6) --------------------------------------------------------


class MfaEnrollRequest(BaseModel):
    """`totp` (the default, and the only method before Phase A10) or `webauthn`."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["totp", "webauthn"] = "totp"


class MfaEnrollResponse(BaseModel):
    """Returned once. For TOTP the URI embeds the shared secret; do not log it. For WebAuthn,
    `options` goes to `navigator.credentials.create({publicKey: options})`."""

    method: Literal["totp", "webauthn"]
    secret: str | None = None
    provisioning_uri: str | None = None
    options: dict[str, Any] | None = None


class MfaVerifyRequest(BaseModel):
    """A TOTP `code`, or a WebAuthn `credential` (a registration or an assertion, whichever
    the session's pending challenge was issued for)."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["totp", "webauthn"] = "totp"
    code: Annotated[str, Field(min_length=6, max_length=10, pattern=r"^[0-9]+$")] | None = None
    credential: dict[str, Any] | None = None
    #: A name for a newly registered key ("YubiKey 5C").
    label: Annotated[str, Field(min_length=1, max_length=100)] | None = None

    @model_validator(mode="after")
    def _one_proof(self) -> MfaVerifyRequest:
        if self.method == "totp" and (self.code is None or self.credential is not None):
            raise ValueError("a TOTP verification needs `code` and nothing else")
        if self.method == "webauthn" and (self.credential is None or self.code is not None):
            raise ValueError("a WebAuthn verification needs `credential`")
        if self.credential is not None and len(json.dumps(self.credential)) > 16_384:
            raise ValueError("credential is too large")
        return self


class MfaVerifyResponse(BaseModel):
    mfa_enabled: bool
    method: Literal["totp", "webauthn"]
    verified_at: dt.datetime
    step_up_expires_at: dt.datetime


class MfaChallengeResponse(BaseModel):
    """For `navigator.credentials.get({publicKey: options})`; single-use, short-lived."""

    options: dict[str, Any]


class MfaFactorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    method: Literal["totp", "webauthn"]
    label: str | None
    confirmed_at: dt.datetime | None
    last_used_at: dt.datetime | None
    created_at: dt.datetime


class MfaResetResponse(BaseModel):
    factors_revoked: int
    sessions_revoked: int


# --- Roles and tenant policies (Sections 2, 3, 7.1; Phase A10) -----------------


class RoleResponse(BaseModel):
    key: str
    permissions: list[str]


class PoliciesResponse(BaseModel):
    client_can_share_dashboards: bool
    developer_can_manage_mcp: bool
    org_admin_requires_webauthn: bool


class PoliciesPatchRequest(BaseModel):
    """Only the fields present change."""

    model_config = ConfigDict(extra="forbid")

    client_can_share_dashboards: bool | None = None
    developer_can_manage_mcp: bool | None = None
    org_admin_requires_webauthn: bool | None = None


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
