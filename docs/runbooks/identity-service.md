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

## Deploy / rollback

1. `alembic upgrade head` as `buvi_migrator` (separate job, Section 26), then roll the deployment.
   Migration `0002_session_token_hash` revokes every session that predates it: all users log in once more.
2. Rollback: redeploy the previous image; run `alembic downgrade -1` only if the migration is confirmed unused.
