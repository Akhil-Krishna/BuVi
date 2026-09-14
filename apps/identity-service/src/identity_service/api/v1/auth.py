"""Authentication endpoints (Sections 6.1, 9).

`GET /auth/login`, `GET /auth/callback`, `POST /auth/logout`, `GET /auth/session`,
plus the MFA pair from Section 9.

Cookie discipline, which is the part a reviewer should check first:

* the session cookie holds the session id and nothing else, and is `HttpOnly`,
  `Secure`, `SameSite=Lax`, path-scoped (Section 6.1 step 6);
* the PKCE transaction cookie holds the verifier, state and nonce, is equally
  locked down, uses `SameSite=Lax` so it survives the IdP's redirect back, and
  is deleted the moment the callback consumes it;
* no token -- access, refresh or ID -- is ever written to a cookie or a response
  body (Section 6.2).
"""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from identity_service.api.v1.schemas import (
    LogoutResponse,
    MfaEnrollResponse,
    MfaVerifyRequest,
    MfaVerifyResponse,
    SessionResponse,
)
from identity_service.core.config import Settings
from identity_service.dependencies import (
    CurrentPrincipal,
    build_auth_service,
    build_mfa_service,
    build_session_service,
    client_ip,
    get_app_settings,
    get_pre_auth_repository,
    get_repository,
)
from identity_service.domain.errors import (
    AuthenticationRequiredError,
    OidcStateMismatchError,
)
from identity_service.infrastructure.db.repositories.identity_repository import (
    IdentityRepository,
)
from platform_auth import STEP_UP_MAX_AGE

router = APIRouter(tags=["auth"])

PreAuthRepo = Annotated[IdentityRepository, Depends(get_pre_auth_repository, scope="function")]
ScopedRepo = Annotated[IdentityRepository, Depends(get_repository, scope="function")]
AppSettings = Annotated[Settings, Depends(get_app_settings)]


def _set_session_cookie(response: Response, settings: Settings, session_id: str) -> None:
    """Section 6.1 step 6: HttpOnly, Secure, SameSite, path-scoped."""
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path=settings.session_cookie_path,
        domain=settings.session_cookie_domain,
        max_age=settings.session_absolute_lifetime_days * 24 * 60 * 60,
    )


def _clear_cookie(response: Response, settings: Settings, name: str) -> None:
    response.delete_cookie(
        key=name,
        path=settings.session_cookie_path,
        domain=settings.session_cookie_domain,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
    )


def _encode_transaction(payload: dict[str, str]) -> str:
    return urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def _decode_transaction(raw: str) -> dict[str, Any]:
    decoded = json.loads(urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("malformed transaction cookie")
    return decoded


def start_login_response(
    request: Request,
    settings: Settings,
    repository: IdentityRepository,
    *,
    status_code: int,
    invitation_token_hash: str | None = None,
) -> Response:
    """Redirect to the IdP with a fresh PKCE challenge and park the transaction.

    The HttpOnly transaction cookie holds the PKCE verifier, state and nonce and,
    for an invitation, only the *hash* of the invitation token (Section 6.2).
    """
    redirect = build_auth_service(request, repository).begin_login()
    payload = {
        "verifier": redirect.challenge.verifier,
        "state": redirect.challenge.state,
        "nonce": redirect.challenge.nonce,
    }
    if invitation_token_hash:
        payload["invitation"] = invitation_token_hash
    response = RedirectResponse(redirect.authorization_url, status_code=status_code)
    response.set_cookie(
        key=settings.oidc_transaction_cookie_name,
        value=_encode_transaction(payload),
        httponly=True,
        secure=settings.session_cookie_secure,
        # Lax, not Strict: the cookie has to survive the IdP's cross-site
        # redirect back to the callback.
        samesite="lax",
        path=settings.session_cookie_path,
        domain=settings.session_cookie_domain,
        max_age=settings.oidc_transaction_ttl_seconds,
    )
    return response


@router.get("/auth/login", include_in_schema=True)
async def login(request: Request, settings: AppSettings, repository: PreAuthRepo) -> Response:
    """**Public.** Redirect to the IdP with a fresh PKCE challenge (Section 9)."""
    return start_login_response(
        request, settings, repository, status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


@router.get("/auth/callback")
async def callback(
    request: Request,
    settings: AppSettings,
    repository: PreAuthRepo,
    code: Annotated[str, Query(min_length=1, max_length=4096)],
    state: Annotated[str, Query(min_length=1, max_length=512)],
) -> Response:
    """**Public.** Complete the flow and open the application session."""
    transaction_cookie = request.cookies.get(settings.oidc_transaction_cookie_name)
    if not transaction_cookie:
        raise OidcStateMismatchError()
    try:
        transaction = _decode_transaction(transaction_cookie)
    except (ValueError, TypeError) as exc:
        raise OidcStateMismatchError() from exc

    service = build_auth_service(request, repository)
    result = await service.complete_login(
        code=code,
        returned_state=state,
        expected_state=str(transaction.get("state", "")),
        verifier=str(transaction.get("verifier", "")),
        nonce=str(transaction.get("nonce", "")),
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        invitation_token_hash=str(transaction.get("invitation") or "") or None,
    )

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_session_cookie(response, settings, result.session_token)
    _clear_cookie(response, settings, settings.oidc_transaction_cookie_name)
    return response


@router.get("/auth/session", response_model=SessionResponse)
async def read_session(
    request: Request,
    principal: CurrentPrincipal,
) -> SessionResponse:
    """The current principal, roles and tenant (Section 9)."""
    user = getattr(request.state, "user", None)
    session_row = getattr(request.state, "session_row", None)
    if user is None:
        raise AuthenticationRequiredError()

    return SessionResponse(
        user_id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        display_name=user.display_name,
        roles=sorted(principal.roles),
        permissions=sorted(principal.permissions),
        auth_method=principal.auth_method,
        mfa_enabled=user.mfa_enabled,
        mfa_verified=principal.mfa_verified,
        step_up_fresh=principal.step_up_is_fresh(),
        session_id=session_row.id if session_row is not None else None,
        expires_at=session_row.expires_at if session_row is not None else None,
    )


@router.post("/auth/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    settings: AppSettings,
    repository: ScopedRepo,
) -> Response:
    """Revoke the session locally, then at the IdP (Section 9)."""
    session_row = getattr(request.state, "session_row", None)
    if session_row is None:
        # An API key has no session to end; refuse rather than silently no-op.
        raise AuthenticationRequiredError()

    service = build_auth_service(request, repository)
    await service.logout(session_row=session_row, ip_address=client_ip(request))

    response = Response(
        content=LogoutResponse().model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )
    _clear_cookie(response, settings, settings.session_cookie_name)
    return response


# --- MFA (Sections 6.6, 9) ----------------------------------------------------


@router.post(
    "/auth/mfa/enroll",
    response_model=MfaEnrollResponse,
    status_code=status.HTTP_201_CREATED,
)
async def enroll_mfa(
    request: Request,
    repository: ScopedRepo,
) -> MfaEnrollResponse:
    """Begin TOTP enrolment (Section 6.6).

    Section 9 marks this endpoint "session, step-up", while Section 7.3's list
    of step-up operations covers changing MFA on *another* user's account. A
    user enrolling for the first time has no MFA with which to satisfy a
    step-up, so requiring one here would make enrolment unreachable. Resolved in
    ADR 0002: first enrolment needs a session; re-enrolment while MFA is already
    enabled is refused here and handled by the Phase A10 reset flow, which is
    step-up protected.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise AuthenticationRequiredError()
    service = build_mfa_service(request, repository)
    challenge = await service.begin_enrollment(user)
    return MfaEnrollResponse(secret=challenge.secret, provisioning_uri=challenge.provisioning_uri)


@router.post("/auth/mfa/verify", response_model=MfaVerifyResponse)
async def verify_mfa(
    request: Request,
    payload: MfaVerifyRequest,
    repository: ScopedRepo,
) -> MfaVerifyResponse:
    """Complete MFA and open the Section 7.3 step-up window."""
    user = getattr(request.state, "user", None)
    session_row = getattr(request.state, "session_row", None)
    if user is None or session_row is None:
        raise AuthenticationRequiredError()

    service = build_mfa_service(request, repository)
    verified_at = await service.verify(user=user, code=payload.code)
    await build_session_service(request, repository).mark_mfa_verified(session_row.id, verified_at)

    return MfaVerifyResponse(
        mfa_enabled=True,
        verified_at=verified_at,
        step_up_expires_at=verified_at + STEP_UP_MAX_AGE,
    )
