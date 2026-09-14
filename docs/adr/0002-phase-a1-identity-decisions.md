# 0002 — Phase A1: identity-service decisions and spec corrections

- **Status:** Accepted · **Date:** 2026-09-14 · **Phase:** A1

Decisions and deviations made while implementing Phase A1. Each fixes a gap, a
contradiction or an error in the v6 spec; none adds a service, role or product feature.

## Spec errors corrected

1. **`minio/minio` no longer pulls from Docker Hub** (Section 27). Compose uses `quay.io/minio/minio` (pinned).
2. **`version: "3.9"`** in the Section 27 compose file is obsolete in Compose v2 and was dropped. Health checks were added.
3. **`identity.roles` `UNIQUE (tenant_id, key)` on a nullable `tenant_id`** (Section 8.1) allows unlimited
   duplicate platform roles under SQL NULL semantics. Implemented as `UNIQUE NULLS NOT DISTINCT` (PG ≥ 15; spec pins 16).
4. **`key_prefix` "first 8 chars, e.g. `sk_live_`"** (Section 8.1) is identical for every key, so it can't
   identify one. Stored as `sk_live_` + 6 random chars; still non-secret (the remaining ~250 bits are argon2-verified).
5. **Section 8 preamble says every table has `updated_at`**, but the Section 8.1 DDL omits it on several
   tables. The explicit DDL wins (Section 0 rule 4); triggers are installed only where the column exists.
6. **Demo and test emails:** `.local`/`.test` are special-use TLDs that `EmailStr` rejects. We use `example.com`.

## Gaps filled

7. **OIDC endpoint ownership.** Section 6.1 describes the Next.js BFF doing the code exchange; Phase A1 puts
   `/auth/*` in identity-service, and `identity.sessions.idp_refresh_token_ref` only makes sense there.
   identity-service owns the exchange and the durable session; the B1 BFF is a thin cookie-holding proxy.
8. **Missing endpoints.** Section 6.9 and Phase A1 require self-service sessions; Section 6.7 and the A1 DoD require
   accepting an invitation. Added `GET /me/sessions`, `DELETE /me/sessions/{id}`, `POST /auth/invitations/accept`
   (public, token-gated like `GET /share/{token}`), plus read-only `GET /admin/users/{id}`, `GET /admin/invitations`.
9. **TOTP storage.** Section 8.1 has only `users.mfa_enabled`. Added `identity.mfa_credentials` (tenant-scoped, RLS)
   holding a Vault `secret_ref` and a replay counter; the secret itself is in Vault.
10. **Step-up recency.** Section 7.3 needs "verified within 5 minutes". Added `sessions.mfa_verified_at` and
    extended `Principal` with `mfa_verified_at`/`roles` (extend, don't rename). `require_step_up` lives in
    `platform-auth` now so Section 9's step-up endpoints aren't shipped unprotected until A10; A10 adds WebAuthn.
11. **First MFA enrolment vs step-up.** Section 9 marks `/auth/mfa/enroll` step-up, but a user with no MFA can't
    satisfy step-up. First enrolment needs a session; re-enrolment while enabled is refused (A10 reset flow).
12. **Email before notification-service (A11).** A narrow `EmailSender` SMTP adapter in identity-service; A11 swaps the implementation.
13. **Secrets client before A3.** Service-local `SecretStore` (Vault KV v2) in `infrastructure/secrets/` per Section 4.1; A3 extracts it.
15. **Keycloak `sslRequired` in local dev.** Keycloak's default `external` treats Docker's port-forwarded
    traffic as non-local and refuses plain HTTP with "HTTPS required". `scripts/keycloak-bootstrap.sh` sets
    `sslRequired=none` on `master` and `buvi` from inside the container. Dev only; staging/prod terminate TLS (Section 28).
    Keycloak 25 also defaults to declarative user profiles: the unmanaged `tenant_slug` attribute is dropped and
    `VERIFY_PROFILE` interrupts login. The bootstrap enables `unmanagedAttributePolicy` and disables `VERIFY_PROFILE`
    so `tenant_slug` reaches the ID token (Section 6.1 step 8). A production realm should instead declare `tenant_slug`
    as a managed, admin-only attribute in its user profile.
14. **Account lockout** (Section 6.5) is enforced by Keycloak brute-force detection, since the IdP owns passwords.

## Row-Level Security design (Section 19)

- Two DB roles: `buvi_migrator` (owns schemas, runs Alembic) and `buvi_app` (request path, owns nothing, RLS applies).
- Policies read `app.tenant_id`; unset means zero rows (deny by default). `FORCE ROW LEVEL SECURITY` on every table.
- **Pre-authentication lookups.** Resolving a session id, an IdP subject, an invitation token or an API key prefix
  establishes the tenant, so it can't itself be tenant-scoped. Those four tables get an extra `FOR SELECT` policy gated on
  `app.pre_auth_lookup`. It can't be used to write (SELECT policies have no WITH CHECK), and each lookup is keyed on a credential.
  As soon as the tenant is known, `bind_tenant` switches the flag off and binds the tenant before any write.
- `audit_events`: UPDATE/DELETE/TRUNCATE revoked from `buvi_app` — append-only enforced by the database.
- Integration tests run the service as `buvi_app`, so a missing tenant binding fails in CI rather than in production.

## Known follow-ups (not A1 scope)

- ~~Session ids are stored as the primary key; a DB read exposes live session ids.~~ **Resolved** by the updated
  Section 8.1 and migration `0002_session_token_hash`: the cookie carries an opaque token, only its SHA-256 is stored,
  and the row id is no longer accepted as a credential. Pre-existing sessions are revoked by that migration.
- `/auth/invitations/accept` takes `idp_subject` from the caller; a stolen token could bind another subject. Mitigated by
  requiring the IdP email to match the user's email at login. Phase B1 should accept invitations only from an authenticated IdP session.
