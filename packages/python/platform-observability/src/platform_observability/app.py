"""FastAPI app-construction helpers shared by every service.

`docs_routes` keeps FastAPI's interactive documentation (`/docs`, `/redoc`, `/openapi.json`)
available where it helps a developer and off where it is public attack surface.

Those three routes are FastAPI built-ins: they are registered outside api-gateway's catalog, so
they are not covered by its authorization or rate-limit pipeline. On an internet-facing edge they
hand an unauthenticated caller the complete endpoint inventory (and `/docs` pulls Swagger UI from
a third-party CDN). The committed `contracts/openapi/*.json` remain the contract of record, and
`app.openapi()` still works in-process, so contract export and drift checks are unaffected.
"""

from __future__ import annotations

from typing import Any, Final

#: Environments where a human is expected to browse the API by hand.
_DEVELOPER_ENVIRONMENTS: Final = frozenset({"dev", "test"})


def docs_routes(environment: str) -> dict[str, Any]:
    """`FastAPI(**docs_routes(settings.environment))`: served in dev/test, disabled beyond."""
    if environment in _DEVELOPER_ENVIRONMENTS:
        return {}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}
