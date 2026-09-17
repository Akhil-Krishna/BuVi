#!/usr/bin/env bash
# Phase A6 DoD scripted flow (Section 32 Steps A-D over HTTP as a client-role user). Needs
# `make up`. Starts every service with captured logs, then runs scripts/test_dashboards.py.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PGCONTAINER="${PGCONTAINER:-buvi-dev-postgres-1}"

"$ROOT/scripts/keycloak-bootstrap.sh" >/dev/null
"$ROOT/scripts/migrate-all.sh" >/dev/null
"$ROOT/scripts/seed-demo-tenant.sh" >/dev/null
"$ROOT/scripts/seed-sample-sales.sh" >/dev/null
docker exec buvi-dev-redis-1 redis-cli FLUSHDB >/dev/null

docker exec -i "$PGCONTAINER" psql -U postgres -d agentic_bi -q -v ON_ERROR_STOP=1 <<'SQL'
DELETE FROM identity.mfa_credentials
  WHERE user_id IN (SELECT id FROM identity.users WHERE email = 'admin@demo.example.com');
UPDATE identity.users SET mfa_enabled = false WHERE email = 'admin@demo.example.com';
DELETE FROM metadata.data_sources WHERE name LIKE 'sample-sales-db%';
SQL

for port in 8000 8001 8002 8003 8004 8005 8006 8007; do
  if curl -sf "http://localhost:${port}/health/live" >/dev/null 2>&1; then
    echo "error: :${port} is already serving; stop it so this flow can capture logs." >&2; exit 1
  fi
done

LOGDIR="$(mktemp -d -t buvi-a6.XXXX)"
PIDS=()
cleanup() { for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; done; }
trap cleanup EXIT

start_service() {  # name, port, env..., command...
  local name="$1" port="$2"; shift 2
  local log="$LOGDIR/$name.log"
  echo "Starting $name on :$port"
  (cd "$ROOT/apps/$name" && exec env "$@") >"$log" 2>&1 &
  PIDS+=("$!")
  for _ in $(seq 1 60); do
    curl -sf "http://localhost:${port}/health/live" >/dev/null 2>&1 && return
    sleep 1
  done
  echo "$name failed to start"; tail -40 "$log"; exit 1
}

start_service identity-service 8001 IDENTITY_REQUIRE_GATEWAY_TOKEN=true IDENTITY_LOG_LEVEL=DEBUG \
  uv run --package identity-service uvicorn identity_service.main:create_app --factory --port 8001
start_service metadata-service 8002 METADATA_REQUIRE_GATEWAY_TOKEN=true METADATA_LOG_LEVEL=DEBUG \
  uv run --package metadata-service uvicorn metadata_service.main:create_app --factory --port 8002
start_service query-gateway 8003 QUERY_GATEWAY_LOG_LEVEL=DEBUG \
  uv run --package query-gateway uvicorn query_gateway.main:create_app --factory --port 8003
start_service api-gateway 8000 GATEWAY_LOG_LEVEL=DEBUG \
  uv run --package api-gateway uvicorn api_gateway.main:create_app --factory --port 8000
start_service visualization-service 8006 VISUALIZATION_LOG_LEVEL=DEBUG \
  uv run --package visualization-service uvicorn visualization_service.main:create_app --factory --port 8006
start_service dashboard-service 8007 DASHBOARD_REQUIRE_GATEWAY_TOKEN=true DASHBOARD_LOG_LEVEL=DEBUG \
  uv run --package dashboard-service uvicorn dashboard_service.main:create_app --factory --port 8007

start_service analytics-orchestrator 8004 ANALYTICS_LOG_LEVEL=DEBUG \
  uv run --package analytics-orchestrator uvicorn analytics_orchestrator.main:create_app --factory --port 8004
start_service worker-runtime 8005 WORKER_LOG_LEVEL=DEBUG \
  uv run --package worker-runtime uvicorn worker_runtime.main:create_app --factory --port 8005

echo "Service logs: $LOGDIR"
cd "$ROOT"
BUVI_SERVICE_LOGS="$LOGDIR" PGCONTAINER="$PGCONTAINER" \
  uv run --package identity-service python scripts/test_dashboards.py
