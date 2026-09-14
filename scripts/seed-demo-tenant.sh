#!/usr/bin/env bash
# Seed the `demo` tenant and its org_admin into Postgres (Phase A1).
#
# Keycloak owns authentication; this service owns the tenant, the user records
# and the role grants. `scripts/keycloak-bootstrap.sh` creates the realm side;
# this creates the matching platform side so the demo admin can invite the other
# two demo users through the real invitation flow.
#
# Only the org_admin is seeded directly. The client and developer users arrive
# through `POST /admin/invitations` -> email -> `POST /auth/invitations/accept`,
# which is what the Phase A1 Definition of Done actually asks to be proven.

set -euo pipefail

PGCONTAINER="${PGCONTAINER:-buvi-dev-postgres-1}"
TENANT_SLUG="${BUVI_TENANT_SLUG:-demo}"
TENANT_NAME="${BUVI_TENANT_NAME:-Demo Organization}"
KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8080}"
REALM="${BUVI_REALM:-buvi}"
ADMIN_USER="${KEYCLOAK_ADMIN:-admin}"
ADMIN_PASS="${KEYCLOAK_ADMIN_PASSWORD:-admin}"

# The platform user row must key on Keycloak's `sub`, so read it back rather
# than inventing one.
TOKEN=$(curl -sf -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" -d "username=${ADMIN_USER}" \
  -d "password=${ADMIN_PASS}" -d "grant_type=password" | jq -r .access_token)

admin_sub=$(curl -sf "${KEYCLOAK_URL}/admin/realms/${REALM}/users?username=demo-admin&exact=true" \
  -H "Authorization: Bearer ${TOKEN}" | jq -r '.[0].id')

[ -n "$admin_sub" ] && [ "$admin_sub" != "null" ] || {
  echo "error: demo-admin not found in realm '${REALM}'. Run scripts/keycloak-bootstrap.sh first." >&2
  exit 1
}

docker exec -i "$PGCONTAINER" psql -U postgres -d agentic_bi -v ON_ERROR_STOP=1 <<SQL
\set QUIET on
BEGIN;

INSERT INTO identity.tenants (name, slug, status, plan, data_region)
VALUES ('${TENANT_NAME}', '${TENANT_SLUG}', 'active', 'trial', 'us')
ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
RETURNING id \gset tenant_

INSERT INTO identity.roles (tenant_id, key, is_system)
SELECT :'tenant_id'::uuid, key, true
FROM (VALUES ('org_admin'), ('developer'), ('client'), ('billing_admin'), ('auditor')) AS r(key)
ON CONFLICT (tenant_id, key) DO NOTHING;

INSERT INTO identity.users (tenant_id, idp_subject, email, display_name, status)
VALUES (
  :'tenant_id'::uuid, '${admin_sub}', 'admin@demo.example.com', 'Alex Demo', 'active'
)
ON CONFLICT (idp_subject) DO UPDATE SET email = EXCLUDED.email, status = 'active'
RETURNING id \gset admin_

INSERT INTO identity.user_roles (user_id, role_id)
SELECT :'admin_id'::uuid, r.id
FROM identity.roles r
WHERE r.tenant_id = :'tenant_id'::uuid AND r.key = 'org_admin'
ON CONFLICT DO NOTHING;

COMMIT;

\echo 'Seeded:'
SELECT '  tenant  ' || slug || '  ' || id FROM identity.tenants WHERE slug = '${TENANT_SLUG}';
SELECT '  admin   ' || email || '  ' || id FROM identity.users WHERE id = :'admin_id'::uuid;
SQL
