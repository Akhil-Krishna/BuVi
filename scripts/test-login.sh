#!/usr/bin/env bash
# Phase A1 DoD scripted flow. Needs `make up`. Idempotent: resets the demo
# invitees and the admin's MFA first, starts identity-service if it is not
# already running, then runs scripts/test_login.py.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PGCONTAINER="${PGCONTAINER:-buvi-dev-postgres-1}"
SERVICE_URL="${IDENTITY_URL:-http://localhost:8001}"

"$ROOT/scripts/keycloak-bootstrap.sh" >/dev/null
"$ROOT/scripts/migrate-all.sh" >/dev/null
"$ROOT/scripts/seed-demo-tenant.sh" >/dev/null

echo "Resetting demo invitees, admin MFA, and MailHog"
docker exec -i "$PGCONTAINER" psql -U postgres -d agentic_bi -q -v ON_ERROR_STOP=1 <<'SQL'
DELETE FROM identity.invitations WHERE email IN ('client@demo.example.com', 'developer@demo.example.com');
DELETE FROM identity.users WHERE email IN ('client@demo.example.com', 'developer@demo.example.com');
DELETE FROM identity.mfa_credentials
  WHERE user_id IN (SELECT id FROM identity.users WHERE email = 'admin@demo.example.com');
UPDATE identity.users SET mfa_enabled = false WHERE email = 'admin@demo.example.com';
SQL
curl -sf -X DELETE "${MAILHOG_URL:-http://localhost:8025}/api/v1/messages" >/dev/null

SERVICE_PID=""
if ! curl -sf "$SERVICE_URL/health/ready" >/dev/null 2>&1; then
  LOG="$(mktemp -t identity-service.XXXX.log)"
  echo "Starting identity-service (log: $LOG)"
  (cd "$ROOT/apps/identity-service" && exec uv run --package identity-service \
     uvicorn identity_service.main:create_app --factory --port 8001) >"$LOG" 2>&1 &
  SERVICE_PID=$!
  trap '[ -n "$SERVICE_PID" ] && kill "$SERVICE_PID" 2>/dev/null || true' EXIT
  for _ in $(seq 1 60); do
    curl -sf "$SERVICE_URL/health/ready" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -sf "$SERVICE_URL/health/ready" >/dev/null || { echo "service failed to start"; tail -40 "$LOG"; exit 1; }
fi

cd "$ROOT"
uv run --package identity-service python scripts/test_login.py
