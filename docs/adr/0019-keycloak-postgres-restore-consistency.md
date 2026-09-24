# 0019. Keycloak and the identity database must be restored as one consistency domain

## Context

While bringing the dev stack back up after Docker Desktop needed a restart (it had been
manually paused, unrelated to this platform), the Phase B1 E2E work hit a login failure that
looked at first like an authorization bug: a valid Keycloak login for `demo-client` reached
identity-service's callback and was refused with

```json
{"error":{"code":"USER_NOT_ACTIVE","message":"This account is not provisioned for any organization."}}
```

That message is `AuthService._resolve_user`'s refusal when an ID token's `tenant_slug` claim is
missing or names a tenant that doesn't exist (`auth_service.py`) — reached only when
`find_user_by_idp_subject(identity.subject)` finds **no existing user**, i.e. the token's
`sub` claim does not match any `identity.users.idp_subject` row.

Comparing the two stores directly confirmed the mechanism, not just the symptom:

```
DB  idp_subject:      client@demo.example.com  c34eb8d9-07d8-4570-951d-86abeb1a8a08
Keycloak current id:  client@demo.example.com  ee1afa41-786d-4db8-b8e6-daaa7d607672
```

**Postgres kept its data across the restart (a named volume); Keycloak's realm did not** — it
came back needing `scripts/keycloak-bootstrap.sh`/the seed scripts to re-run before its users
existed with the right attributes again. For the window in between, and for any real-world
restore that recovers one store to a different point in time than the other, `identity.users`
rows point at Keycloak subjects that no longer exist, and freshly (re)created Keycloak users have
new subjects that match no `identity.users` row. Every affected user is refused login with a
message that reads like a normal authorization decision, not an infrastructure symptom — nothing
about `USER_NOT_ACTIVE` tells an operator that the actual cause is two data stores that fell out
of sync, and this session's own diagnosis needed a direct row-by-row comparison between Postgres
and Keycloak's admin API to find it.

The identity model has exactly one join key between the two stores: `idp_subject`. Neither store
is authoritative over the other's lifecycle — Keycloak owns the subject and the `tenant_slug`
custom attribute a protocol mapper projects into the ID token; `identity.users` owns everything
else about the user (role grants, status, tenant membership) — so restoring them to
different points in time doesn't fail loudly at restore time. It fails later, quietly, per
affected user, at their next login.

## Decision

This is a Phase C1 requirement, not a Track A or Track B one — Track A's local dev/CI workflow
recovers by re-running the seed scripts, which is not available to a production restore.

1. **Backup and restore Keycloak's realm and the identity Postgres schema as one consistency
   domain.** A restore procedure that recovers one without the other to the same point in time is
   incomplete by definition, whatever GA definition of "restore" C1 lands on (a synchronized
   snapshot pair, or a single procedure that recovers both from the platform database's own
   `identity.users`/`identity.sessions` and re-derives Keycloak state from it — not two
   independently-scheduled backup jobs).
2. **The DR restore drill Section 28 requires and `docs/audit/2026-09-20-backend-production-audit.md`
   already tracks for C1 (no PITR, no `docs/runbooks/dr-restore-drill.md` yet) must include this
   specifically**: restore identity's Postgres schema and Keycloak's realm from the same backup
   point and prove a real login for a pre-existing user succeeds afterward — not just that both
   services start.
3. **Consider a startup/health check that detects the desync directly** (e.g. comparing
   `identity.users` count or a sample of `idp_subject` values against the realm's actual users) so
   this fails as a clear, named infrastructure alarm instead of a wave of ordinary-looking
   `USER_NOT_ACTIVE` refusals an on-call engineer has to reverse-engineer. Not required for C1's
   DoD by itself, but cheap next to the diagnostic cost this session paid to find the mechanism
   once.

## Consequences

- No code change follows from this ADR — Track A's actual login/provisioning logic is correct
  given consistent inputs; the gap is entirely in what "restore" means operationally.
- This is the same class of problem ADR 0014 named for the platform database's own recovery
  (Docker Desktop's port forwarder masking the production failure mode from local testing): a
  dev-environment convenience — re-seeding from scripts — currently stands in for a production
  restore procedure that doesn't exist yet.
