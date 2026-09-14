#!/usr/bin/env bash
# Provision the local Keycloak dev realm for identity-service (Phase A1).
#
# Creates:
#   * realm `buvi`, with brute-force detection on (Section 6.5's account-lockout
#     requirement belongs to the IdP, which owns passwords -- this platform
#     never stores one);
#   * confidential client `buvi-platform` with the Authorization Code flow and
#     the test-only direct grant (Section 31.0 permits the latter so Track A can
#     prove login over HTTP without a browser);
#   * the three demo realm roles: client, developer, org_admin;
#   * a `tenant_slug` user attribute plus the protocol mappers that put
#     `tenant_slug` and realm roles into the ID token -- Section 6.1 step 8
#     requires the tenant and roles to arrive as verified claims;
#   * one demo user per role, all in the `demo` tenant.
#
# Idempotent: safe to re-run. Development only -- every password here is a
# throwaway and this script must never be pointed at a real realm.

set -euo pipefail

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8080}"
ADMIN_USER="${KEYCLOAK_ADMIN:-admin}"
ADMIN_PASS="${KEYCLOAK_ADMIN_PASSWORD:-admin}"
REALM="${BUVI_REALM:-buvi}"
CLIENT_ID="${BUVI_CLIENT_ID:-buvi-platform}"
CLIENT_SECRET="${BUVI_CLIENT_SECRET:-dev-client-secret}"
REDIRECT_URI="${BUVI_REDIRECT_URI:-http://localhost:8000/api/v1/auth/callback}"
TENANT_SLUG="${BUVI_TENANT_SLUG:-demo}"
DEMO_PASSWORD="${BUVI_DEMO_PASSWORD:-Demo-Passw0rd!23}"

log() { printf '  %s\n' "$*"; }

require() {
  command -v "$1" >/dev/null 2>&1 || { echo "error: $1 is required" >&2; exit 1; }
}
require curl
require jq

# --- wait for Keycloak ------------------------------------------------------

KC_CONTAINER="${KC_CONTAINER:-buvi-dev-keycloak-1}"
printf 'Waiting for Keycloak container %s ' "$KC_CONTAINER"
for _ in $(seq 1 60); do
  if [ "$(docker inspect -f '{{.State.Health.Status}}' "$KC_CONTAINER" 2>/dev/null)" = "healthy" ]; then
    printf ' ready\n'
    break
  fi
  printf '.'
  sleep 2
done
[ "$(docker inspect -f '{{.State.Health.Status}}' "$KC_CONTAINER" 2>/dev/null)" = "healthy" ] || {
  printf '\n'
  echo "error: Keycloak is not healthy. Is 'make up' running?" >&2
  exit 1
}

# Keycloak's default `sslRequired=external` treats Docker's port-forwarded
# traffic as external and answers "HTTPS required" to the host. Local dev runs
# plain HTTP, so relax it from inside the container. Never do this outside dev.
docker exec "$KC_CONTAINER" bash -c '
  KC=/opt/keycloak/bin/kcadm.sh
  $KC config credentials --server http://localhost:8080 --realm master \
    --user "'"$ADMIN_USER"'" --password "'"$ADMIN_PASS"'" >/dev/null 2>&1
  $KC update realms/master -s sslRequired=NONE
  $KC get realms/'"$REALM"' >/dev/null 2>&1 && $KC update realms/'"$REALM"' -s sslRequired=NONE
  true
' >/dev/null

# --- admin token ------------------------------------------------------------

TOKEN=$(curl -sf -X POST \
  "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" \
  -d "username=${ADMIN_USER}" \
  -d "password=${ADMIN_PASS}" \
  -d "grant_type=password" | jq -r .access_token)

[ -n "$TOKEN" ] && [ "$TOKEN" != "null" ] || { echo "error: could not obtain admin token" >&2; exit 1; }

api() {
  local method="$1" path="$2"
  shift 2
  curl -s -o /dev/null -w '%{http_code}' -X "$method" \
    "${KEYCLOAK_URL}/admin/realms${path}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" "$@"
}

api_get() {
  curl -sf -X GET "${KEYCLOAK_URL}/admin/realms$1" -H "Authorization: Bearer ${TOKEN}"
}

# --- realm ------------------------------------------------------------------

echo "Realm '${REALM}'"
if api_get "/${REALM}" >/dev/null 2>&1; then
  log "already exists"
else
  api POST "" --data @- <<JSON >/dev/null
{
  "realm": "${REALM}",
  "enabled": true,
  "sslRequired": "none",
  "registrationAllowed": false,
  "loginWithEmailAllowed": true,
  "duplicateEmailsAllowed": false,
  "resetPasswordAllowed": true,
  "verifyEmail": false,
  "bruteForceProtected": true,
  "permanentLockout": false,
  "failureFactor": 5,
  "waitIncrementSeconds": 60,
  "maxFailureWaitSeconds": 900,
  "accessTokenLifespan": 300,
  "ssoSessionIdleTimeout": 43200,
  "ssoSessionMaxLifespan": 604800
}
JSON
  log "created (brute-force protection on, 5 failures, exponential backoff)"
fi

# Keycloak 25 ships declarative user profiles: unmanaged attributes such as
# `tenant_slug` are dropped unless the realm allows them, and VERIFY_PROFILE
# interrupts login with a profile page. Both would break the headless flow.
api_get "/${REALM}/users/profile" | jq '. + {unmanagedAttributePolicy: "ENABLED"}' \
  | api PUT "/${REALM}/users/profile" --data @- >/dev/null
api_get "/${REALM}/authentication/required-actions/VERIFY_PROFILE" \
  | jq '.enabled = false | .defaultAction = false' \
  | api PUT "/${REALM}/authentication/required-actions/VERIFY_PROFILE" --data @- >/dev/null
log "user profile: unmanaged attributes enabled, VERIFY_PROFILE disabled"

# --- client -----------------------------------------------------------------

echo "Client '${CLIENT_ID}'"
CLIENT_UUID=$(api_get "/${REALM}/clients?clientId=${CLIENT_ID}" | jq -r '.[0].id // empty')
if [ -z "$CLIENT_UUID" ]; then
  api POST "/${REALM}/clients" --data @- <<JSON >/dev/null
{
  "clientId": "${CLIENT_ID}",
  "enabled": true,
  "protocol": "openid-connect",
  "publicClient": false,
  "secret": "${CLIENT_SECRET}",
  "standardFlowEnabled": true,
  "directAccessGrantsEnabled": true,
  "serviceAccountsEnabled": true,
  "redirectUris": ["${REDIRECT_URI}", "http://localhost:3000/*"],
  "webOrigins": ["http://localhost:3000"],
  "attributes": { "pkce.code.challenge.method": "S256" }
}
JSON
  CLIENT_UUID=$(api_get "/${REALM}/clients?clientId=${CLIENT_ID}" | jq -r '.[0].id')
  log "created (Authorization Code + PKCE S256; direct grant enabled for Track A tests only)"
else
  log "already exists"
fi

# Keep redirect URIs current on existing realms: since Phase A2 the browser-facing
# callback is served through api-gateway (:8000).
api_get "/${REALM}/clients/${CLIENT_UUID}" \
  | jq --arg r "$REDIRECT_URI" '.redirectUris = ([$r, "http://localhost:3000/*"] | unique)' \
  | api PUT "/${REALM}/clients/${CLIENT_UUID}" --data @- >/dev/null
log "redirect URIs: ${REDIRECT_URI}"

# --- protocol mappers -------------------------------------------------------
# Section 6.1 step 8: tenant and roles must arrive as verified token claims.

add_mapper() {
  local name="$1" payload="$2"
  local existing
  existing=$(api_get "/${REALM}/clients/${CLIENT_UUID}/protocol-mappers/models" \
    | jq -r --arg n "$name" '.[] | select(.name == $n) | .id // empty')
  if [ -n "$existing" ]; then
    log "mapper '${name}' already exists"
    return
  fi
  api POST "/${REALM}/clients/${CLIENT_UUID}/protocol-mappers/models" --data "$payload" >/dev/null
  log "mapper '${name}' created"
}

echo "Protocol mappers"
add_mapper "tenant_slug" '{
  "name": "tenant_slug",
  "protocol": "openid-connect",
  "protocolMapper": "oidc-usermodel-attribute-mapper",
  "config": {
    "user.attribute": "tenant_slug",
    "claim.name": "tenant_slug",
    "jsonType.label": "String",
    "id.token.claim": "true",
    "access.token.claim": "true",
    "userinfo.token.claim": "true"
  }
}'
add_mapper "realm-roles" '{
  "name": "realm-roles",
  "protocol": "openid-connect",
  "protocolMapper": "oidc-usermodel-realm-role-mapper",
  "config": {
    "claim.name": "realm_access.roles",
    "jsonType.label": "String",
    "multivalued": "true",
    "id.token.claim": "true",
    "access.token.claim": "true"
  }
}'
add_mapper "audience" '{
  "name": "audience",
  "protocol": "openid-connect",
  "protocolMapper": "oidc-audience-mapper",
  "config": {
    "included.client.audience": "'"${CLIENT_ID}"'",
    "id.token.claim": "true",
    "access.token.claim": "true"
  }
}'

# --- roles ------------------------------------------------------------------
# The three first-class product roles (Section 2). Others exist in the platform
# permission matrix but are not part of the Phase A1 demo tenant.

echo "Realm roles"
for role in client developer org_admin; do
  if api_get "/${REALM}/roles/${role}" >/dev/null 2>&1; then
    log "role '${role}' already exists"
  else
    api POST "/${REALM}/roles" --data "{\"name\":\"${role}\"}" >/dev/null
    log "role '${role}' created"
  fi
done

# --- demo users -------------------------------------------------------------

create_user() {
  local username="$1" email="$2" first="$3" role="$4"
  local user_id
  user_id=$(api_get "/${REALM}/users?username=${username}&exact=true" | jq -r '.[0].id // empty')
  if [ -z "$user_id" ]; then
    api POST "/${REALM}/users" --data @- <<JSON >/dev/null
{
  "username": "${username}",
  "email": "${email}",
  "firstName": "${first}",
  "lastName": "Demo",
  "enabled": true,
  "emailVerified": true,
  "attributes": { "tenant_slug": ["${TENANT_SLUG}"] },
  "credentials": [
    { "type": "password", "value": "${DEMO_PASSWORD}", "temporary": false }
  ]
}
JSON
    user_id=$(api_get "/${REALM}/users?username=${username}&exact=true" | jq -r '.[0].id')
    log "user '${username}' created"
  else
    api_get "/${REALM}/users/${user_id}" \
      | jq --arg e "$email" --arg f "$first" --arg t "$TENANT_SLUG" \
          '.email=$e | .emailVerified=true | .firstName=$f | .lastName="Demo"
           | .requiredActions=[] | .attributes.tenant_slug=[$t]' \
      | api PUT "/${REALM}/users/${user_id}" --data @- >/dev/null
    log "user '${username}' already exists (email/tenant refreshed)"
  fi

  local role_json
  role_json=$(api_get "/${REALM}/roles/${role}")
  api POST "/${REALM}/users/${user_id}/role-mappings/realm" \
    --data "[$(echo "$role_json" | jq -c '{id, name}')]" >/dev/null
  log "  role '${role}' assigned"
}

echo "Demo users (tenant_slug=${TENANT_SLUG})"
create_user "demo-client"    "client@demo.example.com"    "Casey"  "client"
create_user "demo-developer" "developer@demo.example.com" "Devin"  "developer"
create_user "demo-admin"     "admin@demo.example.com"     "Alex"   "org_admin"

cat <<SUMMARY

Keycloak realm '${REALM}' is ready.

  Issuer          ${KEYCLOAK_URL}/realms/${REALM}
  Client          ${CLIENT_ID}
  Demo password   ${DEMO_PASSWORD}
  Demo users      demo-client (client), demo-developer (developer), demo-admin (org_admin)

Next: seed the matching tenant and users in Postgres with
  scripts/seed-demo-tenant.sh
then prove login end-to-end with
  scripts/test-login.sh
SUMMARY
