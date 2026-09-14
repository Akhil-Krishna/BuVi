"""The `/api/v1` router for identity-service.

Route order matches the Section 9 catalog: auth, then admin users and
invitations, then self-service, then audit. Health probes are mounted outside
the versioned prefix by `main.create_app`, because a load balancer should not
have to know about API versions.
"""

from __future__ import annotations

from fastapi import APIRouter

from identity_service.api.v1 import audit, auth, invitations, me, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(invitations.router)
api_router.include_router(users.router)
api_router.include_router(me.router)
api_router.include_router(audit.router)
