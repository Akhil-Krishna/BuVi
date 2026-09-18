#!/usr/bin/env bash
# Phase A1 DoD scripted flow. Needs `make up`. Idempotent: resets the demo
# invitees and the admin's MFA first, starts identity-service if it is not
# already running, then runs scripts/test_login.py.
set -euo pipefail
source "$(dirname "$0")/live-flow.sh"  # flush_redis; this flow keeps its own service handling
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PGCONTAINER="${PGCONTAINER:-buvi-dev-postgres-1}"
SERVICE_URL="${GATEWAY_URL:-http://localhost:8000}"

"$ROOT/scripts/keycloak-bootstrap.sh" >/dev/null
"$ROOT/scripts/migrate-all.sh" >/dev/null
"$ROOT/scripts/seed-demo-tenant.sh" >/dev/null

echo "Resetting demo invitees, admin MFA, rate-limit buckets and MailHog"
# Empty token buckets: the burst checks must not depend on what ran just before.
flush_redis
docker exec -i "$PGCONTAINER" psql -U postgres -d agentic_bi -q -v ON_ERROR_STOP=1 <<'SQL'
DELETE FROM identity.invitations WHERE email IN ('client@demo.example.com', 'developer@demo.example.com');
DELETE FROM identity.users WHERE email IN ('client@demo.example.com', 'developer@demo.example.com');
DELETE FROM identity.mfa_credentials
  WHERE user_id IN (SELECT id FROM identity.users WHERE email = 'admin@demo.example.com');
UPDATE identity.users SET mfa_enabled = false WHERE email = 'admin@demo.example.com';
SQL
curl -sf -X DELETE "${MAILHOG_URL:-http://localhost:8025}/api/v1/messages" >/dev/null

PIDS=()
cleanup() { for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; done; }
trap cleanup EXIT

start_service() {  # name, port, health url, env..., command...
  local name="$1" port="$2"; shift 2
  if curl -sf "http://localhost:${port}/health/live" >/dev/null 2>&1; then
    echo "$name already running on :$port"; return
  fi
  local log; log="$(mktemp -t "$name.XXXX.log")"
  echo "Starting $name on :$port (log: $log)"
  (cd "$ROOT/apps/$name" && exec env "$@") >"$log" 2>&1 &
  PIDS+=("$!")
  for _ in $(seq 1 60); do
    curl -sf "http://localhost:${port}/health/live" >/dev/null 2>&1 && return
    sleep 1
  done
  echo "$name failed to start"; tail -40 "$log"; exit 1
}

# identity-service refuses /api/v1 calls that did not come through the gateway.
start_service identity-service 8001 IDENTITY_REQUIRE_GATEWAY_TOKEN=true \
  uv run --package identity-service uvicorn identity_service.main:create_app --factory --port 8001
start_service api-gateway 8000 \
  uv run --package api-gateway uvicorn api_gateway.main:create_app --factory --port 8000

cd "$ROOT"
GATEWAY_URL="$SERVICE_URL" IDENTITY_DIRECT_URL="http://localhost:8001" \
  uv run --package identity-service python scripts/test_login.py
