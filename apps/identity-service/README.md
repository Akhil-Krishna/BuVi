# identity-service

Users, organizations, roles, invitations, sessions, MFA (TOTP), API keys and the
audit log (build spec Sections 3, 6, 7, 8.1, 9).

| | |
|---|---|
| Owner | platform / identity |
| Schema | `identity` (Section 8.1; migration `migrations/versions/0001_identity_schema.py`) |
| Port | 8001 |
| Health | `GET /health/live`, `GET /health/ready` (checks Postgres) |
| API | `/api/v1` — OpenAPI at `/docs`; exported to `contracts/openapi/` from Phase A2 |
| Dependencies | Postgres (`buvi_app`, RLS-bound), Keycloak (OIDC), Vault (KV v2), SMTP (MailHog locally) |
| MFA | TOTP and WebAuthn (py_webauthn; Phase A10). Step-up is judged by method: WebAuthn is required for `platform_super_admin`, and for `org_admin` under the tenant policy |
| Policies | `GET/PATCH /api/v1/admin/policies` (tenant policies that feed every principal's permissions), `GET /api/v1/admin/roles` |
| Runbook | [`docs/runbooks/identity-service.md`](../../docs/runbooks/identity-service.md) |

## Endpoints

| Method & path | Auth |
|---|---|
| `GET /auth/login`, `GET /auth/callback` | public |
| `POST /invitations/{token}/accept` | public, token-gated; starts IdP login, IdP email must match (Section 6.7) |
| `GET /auth/session`, `POST /auth/logout` | session |
| `POST /auth/mfa/enroll`, `POST /auth/mfa/verify` | session |
| `GET /admin/users`, `GET /admin/users/{id}`, `GET /admin/invitations` | `user:manage` (+ tenant check on `{id}`) |
| `POST /admin/invitations` | `user:manage` + step-up |
| `PATCH /admin/users/{id}/roles` | `role:manage` + tenant check + step-up |
| `POST /admin/users/{id}/sessions/revoke`, `DELETE /admin/users/{id}` | `user:manage` + tenant check + step-up |
| `GET/POST /me/api-keys`, `DELETE /me/api-keys/{id}` | session (owner) or `user:manage` |
| `GET /me/sessions`, `DELETE /me/sessions/{id}` | session (owner) |
| `GET /admin/audit` | `audit:read` |

## Local development

```bash
make up && make migrate && make seed   # infra, schema, Keycloak realm + demo tenant
make dev                               # identity-service on :8001
make test-login                        # Phase A1 scripted end-to-end flow
```

Tests: `uv run pytest apps/identity-service/src/identity_service/tests` (integration
tests start a Postgres container and run the service as the RLS-bound `buvi_app` role).

## Directory and events (Phase A11)

- `POST /internal/v1/directory/users` (`identity-service:directory`) returns a tenant's users with email, status and roles, for notification recipients and seat counts.
- Role changes publish `identity.role.changed` (stream `IDENTITY`) after the commit.
- Invitation email stays here (ADR 0014).
