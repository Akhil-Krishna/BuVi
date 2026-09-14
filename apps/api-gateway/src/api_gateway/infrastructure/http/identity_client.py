"""Authentication through identity-service introspection (Sections 6.1, 6.7, 6.9).

The gateway authenticates every protected request by asking identity-service to
resolve the presented session token or API key. Validating a token locally would be
faster but would miss revocation: Section 6.7 requires a deprovisioned user's
sessions and keys to stop working immediately, not at their next expiry.
"""

from __future__ import annotations

import httpx

from api_gateway.core.config import SCOPE_INTROSPECT
from api_gateway.domain.errors import (
    AuthenticationRequiredError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
    UserNotActiveError,
)
from platform_auth import SERVICE_AUTH_HEADER, Principal, ServiceTokenClient, ServiceTokenError
from platform_observability import REQUEST_ID_HEADER, request_id_var

IDENTITY_AUDIENCE = "identity-service"


class IdentityClient:
    def __init__(
        self, *, base_url: str, http: httpx.AsyncClient, tokens: ServiceTokenClient
    ) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._tokens = tokens

    async def _service_headers(self, scope: str) -> dict[str, str]:
        try:
            token = await self._tokens.token_for(IDENTITY_AUDIENCE, frozenset({scope}))
        except (ServiceTokenError, httpx.HTTPError) as exc:
            raise UpstreamUnavailableError() from exc
        headers = {SERVICE_AUTH_HEADER: f"Bearer {token}"}
        request_id = request_id_var.get()
        if request_id:
            headers[REQUEST_ID_HEADER] = request_id
        return headers

    async def introspect(self, *, session_token: str | None, api_key: str | None) -> Principal:
        payload = {"session_token": session_token} if session_token else {"api_key": api_key}
        try:
            response = await self._http.post(
                f"{self._base}/internal/v1/introspect",
                json=payload,
                headers=await self._service_headers(SCOPE_INTROSPECT),
            )
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError() from exc
        except httpx.TransportError as exc:
            raise UpstreamUnavailableError() from exc

        if response.status_code == 200:
            try:
                return Principal.from_dict(response.json()["principal"])
            except (KeyError, ValueError, TypeError) as exc:
                raise UpstreamUnavailableError() from exc
        code = _error_code(response)
        if response.status_code in (401, 422):
            raise AuthenticationRequiredError()
        if response.status_code == 403 and code == "USER_NOT_ACTIVE":
            raise UserNotActiveError()
        # Anything else (a 403 on the service scope, a 5xx) is a platform fault, not
        # the caller's: never let it read as "you are not authenticated".
        raise UpstreamUnavailableError()

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
