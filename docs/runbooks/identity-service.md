# Runbook: identity-service

**Owner:** platform / identity · **Pages on:** readiness failing, auth failure-rate spike (Section 22.1)

## Health

- `GET /health/live` — process up. Never checks dependencies.
- `GET /health/ready` — `503` when Postgres is unreachable. Remove from rotation; do not restart-loop.

## Common incidents

| Symptom | Likely cause | Action |
|---|---|---|
| Every login fails with `OIDC_EXCHANGE_FAILED` | Keycloak down, client secret rotated, issuer URL mismatch | Check `IDENTITY_OIDC_ISSUER` equals the token `iss`; check Keycloak health; the IdP error body is in logs by `request_id`, never in the response. |
| Login fails with `USER_NOT_ACTIVE` "not provisioned" | Token has no/unknown `tenant_slug` claim, or subject bound to a different email | Check the user's `tenant_slug` attribute and the protocol mapper; compare `identity.users.email` with the IdP email. |
| Users see `SESSION_EXPIRED` unexpectedly | Idle (12h) / absolute (7d) limits, or a forced revoke | Check `identity.sessions.revoked_at` and `audit_events` for `auth.sessions_revoked_for_user`. |
| Writes fail with RLS / "new row violates row-level security" | Code path wrote before binding a tenant | Every pre-auth path must call `IdentityRepository.bind_tenant` before writing. Never grant `BYPASSRLS` to `buvi_app` (Section 19). |
| `503` on ready, DB fine | Pool exhausted | Raise `IDENTITY_DB_POOL_SIZE`; look for long transactions. |
| MFA codes always rejected | Server clock skew, or replayed code | Check NTP; a code is accepted once per 30s step by design. |

## Security operations

- **Force logout a user:** `POST /api/v1/admin/users/{id}/sessions/revoke` (step-up), or in an emergency
  `UPDATE identity.sessions SET revoked_at = now() WHERE user_id = '<id>' AND revoked_at IS NULL;` as `buvi_migrator`.
- **Suspected API key leak:** `DELETE /api/v1/me/api-keys/{id}`; revocation is immediate.
- **Audit:** `identity.audit_events` is append-only (no UPDATE/DELETE for `buvi_app`). Query by `request_id` to join logs.

## Step-up, WebAuthn and tenant policies (Phase A10, ADR 0013)

- **Step-up is judged by method.** Every session records how its last MFA check was made (`totp` or `webauthn`). A `platform_super_admin` must always use WebAuthn. An `org_admin` must too when the tenant policy `org_admin_requires_webauthn` is on. A step-up made with the wrong method is refused with `403 STEP_UP_REQUIRED` and `details.method = "webauthn"`. `GET /auth/session` returns `step_up_method`, so a client knows which factor to ask for.
- **WebAuthn:**
  - enroll with `POST /auth/mfa/enroll {"method":"webauthn"}`, then `POST /auth/mfa/verify` with the browser's credential;
  - step up with `POST /auth/mfa/challenge`, then `/verify`.
  - A challenge is single-use, bound to one session, and expires after `IDENTITY_WEBAUTHN_CHALLENGE_SECONDS`.
  - In production, set `IDENTITY_WEBAUTHN_RP_ID` to the app's registrable domain and `IDENTITY_WEBAUTHN_ORIGINS` to its exact `https://` origins. Startup refuses the development values.
- **`MFA_VERIFICATION_FAILED` spike:** each failure is audited as `auth.mfa_verification_failed` (in its own transaction, so it survives the refused request). A WebAuthn failure after a device change is usually a counter regression (a cloned or reset key). The user removes the key and registers it again.
- **Lost device:** `POST /admin/users/{id}/mfa/reset` (`user:manage`, step-up). It revokes every factor, deletes the secrets in Vault, and ends the user's sessions. The user logs in again and enrolls a first factor, which needs no step-up.
- **An org_admin is stuck behind the WebAuthn policy:** they can still add a key with a fresh TOTP check. Adding a stronger factor never needs the stronger factor. The policy cannot be turned on by an admin who has no key yet (`409 WEBAUTHN_NOT_ENROLLED`).
- **Tenant policies:** `GET/PATCH /admin/policies` (`policy:manage`; a change needs step-up). A change takes effect on each caller's next request, and is audited as `tenant.policies_changed`, with the state before and after.

## Deploy / rollback

1. `alembic upgrade head` as `buvi_migrator` (separate job, Section 26), then roll the deployment.
   Migration `0002_session_token_hash` revokes every session that predates it: all users log in once more.
2. Rollback: redeploy the previous image; run `alembic downgrade -1` only if the migration is confirmed unused.

## Directory and role events (Phase A11)

- **Directory:** `POST /internal/v1/directory/users` (scope `identity-service:directory`: notification-service, analytics-orchestrator) returns a tenant's users with email, status and roles, filtered by id and/or role. It is RLS-bound to the tenant in the request. It is the only way emails leave identity-service.
- **Role events:** a role change that actually changes something publishes `identity.role.changed` (stream `IDENTITY`). It is sent as a background task, after the response and so after the commit, so a rolled-back change is never announced.
- **Best effort:** without NATS, login and admin work are unaffected; only the notification is lost. `IDENTITY_EVENTS_ENABLED=false` turns it off.
- **Invitation email** stays here, not in notification-service (ADR 0014): it carries a one-time token.

## Deactivation cascade (Section 6.7)

- **Order:** `DELETE /admin/users/{id}` first revokes the user's share links in dashboard-service (`dashboard-service:user-lifecycle`), then deactivates the user and revokes their sessions and API keys.
- **If dashboard-service is unreachable:** the call answers `503 UPSTREAM_UNAVAILABLE` and nothing changes. Restore dashboard-service and retry; the cascade is idempotent.
- **Seats:** `POST /internal/v1/directory/seats` returns the exact active-user count. `directory/users` is capped at 5,000 with `truncated: true` (pagination is a Phase C1 item).
