"""Authentication through identity-service introspection (Sections 6.1, 6.7, 6.9).

The gateway authenticates every protected request by asking identity-service to
resolve the presented session token or API key. Validating a token locally would be
faster but would miss revocation: Section 6.7 requires a deprovisioned user's
sessions and keys to stop working immediately, not at their next expiry.

The call itself is `platform_auth.IntrospectionClient`, shared with every service that
re-authenticates the forwarded credential (Section 6.3); this adapter maps its typed
outcomes onto the gateway's error envelope.
"""

from __future__ import annotations

import httpx

from api_gateway.domain.errors import (
    AuthenticationRequiredError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
    UserNotActiveError,
)
from platform_auth import (
    CredentialRejectedError,
    IdentityTimeoutError,
    IntrospectionClient,
    IntrospectionError,
    Principal,
    PrincipalNotActiveError,
    ServiceTokenClient,
)


class IdentityClient:
    def __init__(
        self, *, base_url: str, http: httpx.AsyncClient, tokens: ServiceTokenClient
    ) -> None:
        self._client = IntrospectionClient(base_url=base_url, http=http, tokens=tokens)

    async def introspect(self, *, session_token: str | None, api_key: str | None) -> Principal:
        try:
            return await self._client.introspect(session_token=session_token, api_key=api_key)
        except CredentialRejectedError:
            raise AuthenticationRequiredError() from None
        except PrincipalNotActiveError:
            raise UserNotActiveError() from None
        except IdentityTimeoutError as exc:
            raise UpstreamTimeoutError() from exc
        except IntrospectionError as exc:
            # Never let a platform fault read as "you are not authenticated".
            raise UpstreamUnavailableError() from exc

    async def ready(self) -> bool:
        return await self._client.ready()
