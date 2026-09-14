"""Request correlation middleware (Section 22).

At the edge (api-gateway) the inbound `X-Request-ID` is ignored and a fresh id is
minted: a client must not choose the id that joins its request to logs and audit
rows. Internal services trust the id their caller forwarded, after validating its
shape, so one request stays traceable across hops.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Final

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from platform_observability.logging import request_id_var

REQUEST_ID_HEADER: Final = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9_\-]{8,128}$")


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


class RequestIdMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, trust_inbound: bool) -> None:
        super().__init__(app)
        self._trust_inbound = trust_inbound

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        inbound = request.headers.get(REQUEST_ID_HEADER, "")
        if self._trust_inbound and _VALID_REQUEST_ID.match(inbound):
            request_id = inbound
        else:
            request_id = new_request_id()
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
