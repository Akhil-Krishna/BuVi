# 0003 — Phase A2: api-gateway decisions

- **Status:** Accepted · **Date:** 2026-09-14 · **Phase:** A2

## Decisions

1. **Authentication by introspection, not local JWT validation.** The gateway resolves the session
   cookie token or API key by calling identity-service `POST /internal/v1/introspect` on every protected
   request. Section 6.1 steps 7–8 describe the gateway validating an IdP access token locally, but
   Sections 6.7 and 6.9 require revocation and deprovisioning to take effect immediately, and Section 2
   requires permissions to come from platform data, not token claims. Local JWT validation could satisfy
   neither without an introspection call anyway. Raw Keycloak access tokens are not accepted at the
   gateway. Track B's BFF forwards the session cookie token (ADR 0002 item 7). **Spec follow-up:**
   Section 6.1 steps 7–8 should be reworded by the spec owner to match.

2. **Service-to-service auth (Section 6.3).** identity-service issues RS256 JWTs through a
   client-credentials grant (`POST /internal/v1/oauth/token`, form-encoded) and publishes
   `GET /internal/v1/jwks.json`. Tokens carry `aud` = the callee and narrow `scope`
   (`identity-service:introspect`, `<service>:proxy`). They travel in `X-Service-Authorization`, never
   `Authorization`, because a proxied request also carries the user's credential. Signing, verification
   and the token client live in `platform-auth`. An unset signing key means an ephemeral key, allowed
   only in dev/test; staging/prod refuse to start without one.

3. **identity-service behind the gateway.** `IDENTITY_REQUIRE_GATEWAY_TOKEN` (refused off in
   staging/prod) makes every `/api/v1` call require a gateway token. A present-but-invalid token is
   always rejected. `X-Forwarded-For` is trusted only on requests carrying a valid gateway token, which
   closes the ADR 0002 note that audit IPs were the socket peer.

4. **Public OIDC callback moves to the gateway** (`http://localhost:8000/api/v1/auth/callback`). The
   Keycloak bootstrap updates existing clients' redirect URIs.

5. **Rate limiting (Sections 5, 20, 24).** Redis token buckets. One Lua script per bucket uses Redis
   `TIME`, so replicas share an atomic, clock-skew-free count. Tiers: per-IP `auth` (10, 0.2/s) for login,
   callback, invitation acceptance and MFA verify; per-IP `public` (60, 1/s); per-user (120, 2/s);
   per-tenant (1000, 20/s). IP limits apply before authentication, so a burst costs no introspection.
   **Amended after Phase A8:** authenticated routes no longer draw from the `public` IP bucket. That
   bucket capped every signed-in user behind one NAT or corporate proxy at 1 request/s combined,
   and a dashboard load exceeds that. They get their own per-IP flood guard, `authenticated`
   (1000, 20/s, the tenant tier's size). Fair use per person remains the user bucket's job. Every
   public route must name a strict tier (`auth` or `public`), which a catalog test enforces.
   **Fail-open** by default: a Redis outage must not take down the API. It is logged and reported as
   `degraded`, and can be switched to fail-closed during an attack.

6. **Coarse checks at the gateway, resource checks downstream.** From Section 9: permission, role and
   step-up. Section 7.2 resource-tenant checks stay in owning services. Where Section 9 is imprecise:
   - `POST /dashboards` ("create implicit in role") → `dashboard:pin` (every role that can pin can create;
     `auditor` cannot).
   - `GET /sql/history` → either `sql:execute` or `run:debug`.
   - `POST /mcp/servers/{id}/approve`, `POST /admin/webhooks` → role `org_admin`.
   - `POST /mcp/servers/{id}/tools/{tool}/invoke` → authenticated only; the tool grant is checked by
     mcp-gateway.
   - `GET /billing/usage`, `POST /billing/subscription`: Section 3 assigns no owning service. They are
     stubbed with no backend, to be resolved by Phase A11.

7. **Stubs.** Routes whose owner does not exist yet answer `501 NOT_IMPLEMENTED` with
   `details.available_in_phase`, after rate limiting, authentication and authorization, so a caller
   without permission gets `403`, not a hint about the roadmap.

8. **Shared error envelope and correlation** (`platform-observability`): `ApiError`,
   `install_error_handlers`, `RequestIdMiddleware`, JSON logging and redaction. identity-service now uses
   them too. The edge mints the request id; internal services trust a validated forwarded id. Uvicorn
   access logging is disabled platform-wide, because `/invitations/{token}/accept` carries a credential in
   its path (ADR 0002).

9. **Contracts.** `scripts/gen-openapi.sh` exports every service to `contracts/openapi/`. The gateway
   composes request/response bodies from committed downstream contracts (file-based, no code coupling).
   `scripts/diff-contracts.sh` (CI job `contracts`) fails on drift, and on breaking changes (removed
   operation, new required parameter/body, removed success response) without a major version bump. This
   completes ADR 0001 item 5.

10. **No migrations.** The gateway is stateless (Section 3), so there is no `migrations/` or
    `alembic.ini`; the rest of Section 4.1's structure applies.

## Spec errata

- §9 preamble: "error envelope in Section 22" → Section 21 (already in ADR 0001's errata table).

## Follow-ups

- Introspection adds one hop per request. Any cache must be invalidated by revocation events
  (Section 18.1), not a TTL alone.
- SSE passthrough for `GET /runs/{id}/events` lands with Phase A5.
- Full request/response schema enforcement at the gateway (Section 3) once owning services publish
  contracts (A12 client generation depends on it).
