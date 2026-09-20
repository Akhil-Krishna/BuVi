# 0018. Phase B1: the real BFF auth architecture, and the redirect_uri allow-list

## Context

Section 6.1, as written before Track A existed, describes Next.js itself as the OIDC relying
party: building the PKCE authorization request, redirecting to Keycloak, and — critically —
"exchang[ing] the code for tokens directly with Keycloak's token endpoint" from Next.js's own
server. Track A did not build it that way, and the difference is not cosmetic.

Reading the actual Phase A1 implementation (`apps/identity-service/src/identity_service/api/v1/
auth.py`, `application/services/auth_service.py`, `infrastructure/oidc/client.py`) and the
scripted DoD flow that exercises it (`scripts/test_login.py`) end to end:

- **identity-service is the OIDC relying party**, not Next.js. It holds the Keycloak client
  secret, builds the PKCE challenge, redirects to Keycloak, and exchanges the code server-side.
  Next.js was never going to talk to Keycloak's token endpoint directly.
- `GET /auth/callback` returns **`204 No Content`** with `Set-Cookie: buvi_session=...` — a
  shape meant for a server-side caller to read and relay, not for a real browser's top-level
  navigation to land on and render.
- `identity_service.core.config.Settings.oidc_redirect_uri` defaults to
  `http://localhost:8000/api/v1/auth/callback` — **api-gateway's own origin**, not Next.js's.
  This is what `scripts/test_login.py` drives directly, and what the Phase A1 DoD proves.
- The live Keycloak client (`buvi-platform`, confirmed against the running dev realm) already
  has **both** `http://localhost:8000/api/v1/auth/callback` and `http://localhost:3000/*`
  registered as valid redirect URIs — `scripts/keycloak-bootstrap.sh` provisioned the Next.js
  wildcard before any frontend code existed, anticipating exactly this.
- `api-gateway`'s request pipeline (`application/services/request_pipeline.py::credentials_of`)
  accepts the **`buvi_session` cookie directly** and introspects it server-to-server against
  identity-service. There is no separate access-token/Bearer translation step for the BFF to
  perform — Section 6.1 step 7's "attaches a short-lived access token (or performs on-behalf-of
  token exchange)" does not describe what was actually built. The real mechanism is simpler:
  forward the cookie.
- `proxy.ts` (not `middleware.ts`) is not a spec idiosyncrasy — Next.js 16 genuinely renamed
  Middleware to Proxy (confirmed against Next.js's own docs before writing any code, given how
  often framework APIs drift under a stale training prior). `package.json` already pins
  `next@16.3.5`, so this spelling is correct as written.

## Decision

**Next.js owns exactly two thin server-side relay routes; identity-service owns the entire OIDC
handshake.** The browser only ever talks to the Next.js origin and, unavoidably, Keycloak's own
login UI — it never navigates to or fetches api-gateway directly, consistent with the BFF
principle stated everywhere else in this document (and with the earlier backend audit's CORS
finding, which concerned `fetch`/`XHR`, not navigation, and stands either way).

1. **`(auth)/login/route.ts`** — a GET route handler. Server-side, it calls
   `GET {GATEWAY_URL}/api/v1/auth/login?redirect_uri={NEXT_CALLBACK_URL}` with redirects disabled,
   reads the `307`'s `Location` (Keycloak's authorization URL) and `Set-Cookie`
   (`buvi_oidc_txn`), and returns its own `307` to the browser: the same Location, and the same
   transaction cookie re-issued on Next.js's own origin (host-only, no `Domain` attribute).
2. **`(auth)/callback/route.ts`** — the real, Keycloak-registered redirect target for the
   Next.js-driven flow. It reads `?code&state`, forwards the transaction cookie it received from
   the browser as a `Cookie` header in a server-side call to
   `GET {GATEWAY_URL}/api/v1/auth/callback?code&state`, reads the `204`'s `Set-Cookie`
   (`buvi_session`), sets it on its own response (again host-only), clears the transaction
   cookie, and **redirects the browser to the app** (a real page, unlike the 204 it relayed).
3. **`app/api/[...path]/route.ts`** (the generic BFF proxy, per Section 4.2) forwards the
   incoming request's `buvi_session` cookie as a `Cookie` header to api-gateway. No JWT/access
   token handling exists in Next.js at any point.
4. **`proxy.ts`** is a lightweight, presence-only gate: no `buvi_session` cookie → redirect to
   `(auth)/login`. It does not decode or validate the cookie (that requires a network call, and
   Section 4.2 already says this file "MUST NOT be the sole authorization layer"). Role-based
   redirects (e.g., a `client` hitting `/admin/*`) are each route group's own concern, resolved
   from `GET /auth/session`, not `proxy.ts`'s.

## The `redirect_uri` allow-list (why this needed a real code change, not just Next.js glue)

`identity_service`'s OIDC client used one fixed `redirect_uri` for both the authorization request
and the token exchange (OAuth requires them to match). Simply repointing that fixed value at
Next.js's callback would have broken `scripts/test_login.py`'s existing, passing Phase A1 DoD
flow, which drives `/api/v1/auth/login` → `/api/v1/auth/callback` directly and asserts the exact
callback path in the redirect Location. Track A stays green; this could not be a silent global
change (rule 11, Phase 5).

Instead, `GET /auth/login` (and `POST /invitations/{token}/accept`, which starts the same login)
now accepts an optional `redirect_uri` query parameter, checked against a **two-value allow-list**
(`Settings.oidc_redirect_uri`, the existing default, and the new `Settings.
oidc_frontend_redirect_uri`, defaulting to `http://localhost:3000/callback`) before it is ever
embedded in a redirect to the IdP — anything else is `400 INVALID_REDIRECT_URI`, checked and
proven rejected before any cookie is set or the IdP is contacted. This is deliberately not a
general allow-list mechanism: exactly two legal values, matching the exact two callers that
exist. The chosen value is parked in the (unsigned, HttpOnly, short-TTL) transaction cookie
alongside the existing verifier/state/nonce and re-validated at the callback, so a request that
never went through `begin_login` cannot spend a real authorization code against a URI this
service does not recognize.

Omitting the parameter reproduces the exact previous behavior — verified by running the
pre-existing identity-service suite (257 tests, zero regressions), `scripts/test-login.sh` (the
live, Keycloak-backed Phase A1 DoD flow, unaffected), and 3 new tests added for the allow-list
itself (260 total).

## Consequences

- No Keycloak realm changes were needed — the wildcard redirect URI was already provisioned.
- No `session_cookie_domain` cross-origin sharing is required in dev or prod: each origin
  (api-gateway, Next.js) sets and reads its own cookie; only the *value* is relayed server-to-
  server, never the cookie attributes.
- Section 6.1 is corrected to describe this architecture instead of the pre-Track-A assumption.
- `docs/architecture/Agentic_BI_Platform_Build_Spec.md` Section 4.2's `app/api/[...path]/route.ts`
  comment ("injects session token server-side") is accurate as a *description* of forwarding the
  session cookie; it is not a JWT-minting step.
