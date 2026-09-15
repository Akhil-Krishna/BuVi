"""Authenticating a forwarded credential through identity-service (Sections 6.1, 6.3, 6.9).

Every service that authenticates end users resolves the presented session token or
API key by calling identity-service `POST /internal/v1/introspect` with its own
service token. Local token validation would miss revocation, which Sections 6.7 and
6.9 require to take effect immediately (ADR 0003 item 1).

Failures are typed so each caller maps them onto its own error envelope: a platform
fault must never read to the user as "you are not authenticated".
"""

from __future__ import annotations

from typing import Final

import httpx
from starlette.requests import Request

from platform_auth.principal import Principal
from platform_auth.service_tokens import SERVICE_AUTH_HEADER, ServiceTokenClient, ServiceTokenError
from platform_observability import REQUEST_ID_HEADER, request_id_var

IDENTITY_AUDIENCE: Final = "identity-service"
SCOPE_INTROSPECT: Final = "identity-service:introspect"
BEARER_PREFIX: Final = "Bearer "
MAX_CREDENTIAL_LENGTH: Final = 512


class IntrospectionError(Exception):
    """identity-service could not vouch for the caller, for a reason that is not theirs."""


class CredentialRejectedError(IntrospectionError):
    """The credential is unknown, expired or revoked. Callers answer 401."""


class PrincipalNotActiveError(IntrospectionError):
    """The credential is valid but the account is not active. Callers answer 403."""


class IdentityTimeoutError(IntrospectionError):
    """identity-service did not answer in time. Callers answer 504."""


def request_credentials(request: Request, cookie_name: str) -> tuple[str | None, str | None]:
    """(session token, API key): the session cookie first, then `Authorization: Bearer`."""
    cookie = request.cookies.get(cookie_name)
    if cookie:
        return cookie, None
    authorization = request.headers.get("authorization", "")
    if authorization.startswith(BEARER_PREFIX):
        return None, authorization[len(BEARER_PREFIX) :].strip() or None
    return None, None


class IntrospectionClient:
    """Calls identity-service on behalf of one service, with that service's token."""

    def __init__(
        self, *, base_url: str, http: httpx.AsyncClient, tokens: ServiceTokenClient
    ) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._tokens = tokens

    @property
    def base_url(self) -> str:
        return self._base

    async def service_headers(self, scope: str) -> dict[str, str]:
        """Headers for a call to identity-service: a scoped service token and the request id."""
        try:
            token = await self._tokens.token_for(IDENTITY_AUDIENCE, frozenset({scope}))
        except (ServiceTokenError, httpx.HTTPError, KeyError, ValueError) as exc:
            raise IntrospectionError("service token unavailable") from exc
        headers = {SERVICE_AUTH_HEADER: f"Bearer {token}"}
        request_id = request_id_var.get()
        if request_id:
            headers[REQUEST_ID_HEADER] = request_id
        return headers

    async def introspect(self, *, session_token: str | None, api_key: str | None) -> Principal:
        credential = session_token or api_key
        if not credential or len(credential) > MAX_CREDENTIAL_LENGTH:
            raise CredentialRejectedError()
        payload = {"session_token": session_token} if session_token else {"api_key": api_key}
        headers = await self.service_headers(SCOPE_INTROSPECT)
        try:
            response = await self._http.post(
                f"{self._base}/internal/v1/introspect", json=payload, headers=headers
            )
        except httpx.TimeoutException as exc:
            raise IdentityTimeoutError() from exc
        except httpx.TransportError as exc:
            raise IntrospectionError("identity-service unreachable") from exc

        if response.status_code == 200:
            try:
                return Principal.from_dict(response.json()["principal"])
            except (KeyError, ValueError, TypeError) as exc:
                raise IntrospectionError("malformed introspection response") from exc
        if response.status_code in (401, 422):
            raise CredentialRejectedError()
        if response.status_code == 403 and _error_code(response) == "USER_NOT_ACTIVE":
            raise PrincipalNotActiveError()
        # A 403 on the service scope, or a 5xx, is a platform fault.
        raise IntrospectionError(f"introspection returned {response.status_code}")

    async def ready(self) -> bool:
        try:
            response = await self._http.get(f"{self._base}/health/ready", timeout=2.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200


def _error_code(response: httpx.Response) -> str | None:
    try:
        return str(response.json()["error"]["code"])
    except (ValueError, KeyError, TypeError):
        return None
