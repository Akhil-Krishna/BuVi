"""Phase A1 Definition of Done: the scripted end-to-end flow, over HTTP only.

For all three demo roles: invite -> email lands in MailHog -> accept (email
verification) -> log in through Keycloak's real login form (Authorization Code
+ PKCE, no browser) -> inspect the session cookie at the HTTP layer -> check
`/auth/session` tenant and permissions -> log out -> confirm the session is dead.
The admin additionally enrolls TOTP (invitations are step-up operations),
changes a role, and confirms login/logout/role-change audit events exist.

Run via `scripts/test-login.sh`, which prepares the stack and the service.
"""

from __future__ import annotations

import html
import os
import quopri
import re
import sys
import time

import httpx
import pyotp

SERVICE = os.environ.get("IDENTITY_URL", "http://localhost:8001")
KEYCLOAK = os.environ.get("KEYCLOAK_URL", "http://localhost:8080")
MAILHOG = os.environ.get("MAILHOG_URL", "http://localhost:8025")
REALM = os.environ.get("BUVI_REALM", "buvi")
PASSWORD = os.environ.get("BUVI_DEMO_PASSWORD", "Demo-Passw0rd!23")

USERS = {
    "org_admin": ("demo-admin", "admin@demo.example.com"),
    "developer": ("demo-developer", "developer@demo.example.com"),
    "client": ("demo-client", "client@demo.example.com"),
}
# (permission the role must hold, permission it must not hold) -- Section 7.1
EXPECTED = {
    "org_admin": ("user:manage", None),
    "developer": ("sql:execute", "user:manage"),
    "client": ("chat:use", "sql:execute"),
}

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f"  -- {detail}"))
    if not ok:
        failures.append(name)
    return ok


def set_cookie(response: httpx.Response, name: str) -> str:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    return ""


def cookie_value(header: str) -> str:
    return header.split("=", 1)[1].split(";", 1)[0]


def login(username: str) -> tuple[str, str]:
    """Drive the real Section 6.1 flow headlessly. Returns (session id, Set-Cookie)."""
    with httpx.Client(base_url=SERVICE, timeout=15) as svc:
        start = svc.get("/api/v1/auth/login")
        if start.status_code != 307:
            raise RuntimeError(f"/auth/login returned {start.status_code}")
        txn = cookie_value(set_cookie(start, "buvi_oidc_txn"))

        with httpx.Client(timeout=15, follow_redirects=True) as kc:
            page = kc.get(start.headers["location"])
            form = re.search(r"<form[^>]*kc-form-login[^>]*>", page.text)
            action = re.search(r'action="([^"]+)"', form.group(0)) if form else None
            if not action:
                raise RuntimeError("Keycloak login form not found")
            submitted = kc.post(
                html.unescape(action.group(1)),
                data={"username": username, "password": PASSWORD, "credentialId": ""},
                follow_redirects=False,
            )
        location = submitted.headers.get("location", "")
        if "/api/v1/auth/callback" not in location:
            raise RuntimeError(f"no callback redirect ({submitted.status_code}): {location[:200]}")

        callback = svc.get(
            location[location.index("/api/v1/auth/callback") :],
            headers={"Cookie": f"buvi_oidc_txn={txn}"},
        )
        if callback.status_code != 204:
            raise RuntimeError(f"/auth/callback returned {callback.status_code}: {callback.text}")
        header = set_cookie(callback, "buvi_session")
        return cookie_value(header), header


def api(method: str, path: str, session: str | None = None, **kwargs: object) -> httpx.Response:
    headers = {"Cookie": f"buvi_session={session}"} if session else {}
    with httpx.Client(base_url=SERVICE, timeout=15) as svc:
        return svc.request(method, path, headers=headers, **kwargs)  # type: ignore[arg-type]


def keycloak_subject(username: str) -> str:
    token = httpx.post(
        f"{KEYCLOAK}/realms/master/protocol/openid-connect/token",
        data={
            "client_id": "admin-cli",
            "username": "admin",
            "password": "admin",
            "grant_type": "password",
        },
    ).json()["access_token"]
    users = httpx.get(
        f"{KEYCLOAK}/admin/realms/{REALM}/users",
        params={"username": username, "exact": "true"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    return str(users[0]["id"])


def mailed_token(email: str) -> str | None:
    for _ in range(20):
        items = httpx.get(f"{MAILHOG}/api/v2/search", params={"kind": "to", "query": email}).json()
        for item in items.get("items") or []:
            body = item["Content"]["Body"]
            encoding = item["Content"]["Headers"].get("Content-Transfer-Encoding", [""])[0]
            if "quoted-printable" in encoding.lower():
                body = quopri.decodestring(body.encode()).decode()
            match = re.search(r"token=([A-Za-z0-9_\-]+)", body)
            if match:
                return match.group(1)
        time.sleep(0.5)
    return None


def check_cookie(role: str, header: str) -> None:
    lowered = header.lower()
    check(f"{role}: session cookie HttpOnly", "httponly" in lowered, header)
    check(f"{role}: session cookie Secure", "secure" in lowered, header)
    check(f"{role}: session cookie SameSite=Lax", "samesite=lax" in lowered, header)
    check(f"{role}: cookie carries no token", "token" not in lowered, header)


def check_session(role: str, session: str, tenant_id: str | None) -> dict[str, object]:
    response = api("GET", "/api/v1/auth/session", session)
    body = response.json() if response.status_code == 200 else {}
    check(f"{role}: GET /auth/session 200", response.status_code == 200, response.text)
    if tenant_id:
        check(f"{role}: tenant_id matches demo tenant", body.get("tenant_id") == tenant_id)
    check(f"{role}: roles == [{role}]", body.get("roles") == [role], str(body.get("roles")))
    has, lacks = EXPECTED[role]
    perms = body.get("permissions") or []
    check(f"{role}: has {has}", has in perms)
    if lacks:
        check(f"{role}: lacks {lacks}", lacks not in perms)
    return body


def logout(role: str, session: str) -> None:
    check(
        f"{role}: POST /auth/logout 200",
        api("POST", "/api/v1/auth/logout", session).status_code == 200,
    )
    after = api("GET", "/api/v1/auth/session", session)
    check(
        f"{role}: session dead after logout (401)", after.status_code == 401, str(after.status_code)
    )


def main() -> int:
    print("org_admin: log in")
    admin_username, _ = USERS["org_admin"]
    admin, header = login(admin_username)
    check_cookie("org_admin", header)
    tenant_id = str(check_session("org_admin", admin, None).get("tenant_id"))

    print("org_admin: enroll TOTP (invitations require step-up, Section 7.3)")
    enroll = api("POST", "/api/v1/auth/mfa/enroll", admin)
    check("org_admin: MFA enroll 201", enroll.status_code == 201, enroll.text)
    code = pyotp.TOTP(enroll.json()["secret"]).now()
    verify = api("POST", "/api/v1/auth/mfa/verify", admin, json={"code": code})
    check("org_admin: MFA verify 200", verify.status_code == 200, verify.text)

    user_ids: dict[str, str] = {}
    for role in ("developer", "client"):
        username, email = USERS[role]
        print(f"{role}: invite -> MailHog -> accept -> login -> logout")
        invite = api(
            "POST", "/api/v1/admin/invitations", admin, json={"email": email, "role_key": role}
        )
        check(f"{role}: invitation created 201", invite.status_code == 201, invite.text)
        token = mailed_token(email)
        if not check(f"{role}: invitation email delivered to MailHog", token is not None):
            continue
        accept = api(
            "POST",
            "/api/v1/auth/invitations/accept",
            json={"token": token, "idp_subject": keycloak_subject(username)},
        )
        check(f"{role}: invitation accepted 201", accept.status_code == 201, accept.text)
        user_ids[role] = accept.json().get("id", "")
        reuse = api(
            "POST",
            "/api/v1/auth/invitations/accept",
            json={"token": token, "idp_subject": "someone-else"},
        )
        check(f"{role}: invitation token single-use (400)", reuse.status_code == 400)

        session, header = login(username)
        check_cookie(role, header)
        check_session(role, session, tenant_id)
        if role == "client":
            denied = api("GET", "/api/v1/admin/users", session)
            check("client: GET /admin/users forbidden (403)", denied.status_code == 403)
        logout(role, session)

    print("org_admin: change a role, read the audit log, log out")
    client_id = user_ids.get("client")
    if client_id:
        grant = api(
            "PATCH", f"/api/v1/admin/users/{client_id}/roles", admin, json={"grant": ["developer"]}
        )
        check("org_admin: grant role 200", grant.status_code == 200, grant.text)
        revoke = api(
            "PATCH", f"/api/v1/admin/users/{client_id}/roles", admin, json={"revoke": ["developer"]}
        )
        check("org_admin: revoke role 200", revoke.status_code == 200, revoke.text)

    audit = api("GET", "/api/v1/admin/audit", admin, params={"limit": 200})
    types = {item["event_type"] for item in audit.json().get("items", [])}
    for event in (
        "auth.login",
        "auth.logout",
        "user.role_changed",
        "user.invited",
        "user.invitation_accepted",
        "auth.mfa_enabled",
    ):
        check(f"audit: {event} recorded", event in types)
    logout("org_admin", admin)

    print(f"\n{'PASSED' if not failures else 'FAILED'}: {len(failures)} failure(s)")
    for name in failures:
        print(f"  - {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
