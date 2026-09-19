# Shared setup for the scripted DoD flows (scripts/test-*.sh). Source it; do not run it.
#
# Every flow starts from the same known state, so flows can run in any order:
#   flush_redis        empty the Redis the services use (rate-limit buckets, sessions, ...)
#   reset_demo_state   bootstrap + migrate + seed, empty Redis (rate-limit buckets, sessions,
#                      idempotency records), then undo every change a flow may leave behind and
#                      verify the result (assert_demo_baseline). Every flow calls it first --
#                      tests/test_repo_structure.py enforces that -- so a flow that fails midway
#                      cannot change how the next one behaves. A flow that changes shared state
#                      adds the undo here and the check to assert_demo_baseline.
#   start_services     start services by name, logs in $LOGDIR, refusing ports already in use
#                      (a flow inspects the logs of the processes it started, not someone else's)
# Services are stopped when the flow exits.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGCONTAINER="${PGCONTAINER:-buvi-dev-postgres-1}"
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"  # what every service defaults to
export PGCONTAINER

# Empty the Redis the services use -- only after proving it is the compose Redis. Another
# redis-server on the host can shadow it on 127.0.0.1:6379; the flows then refuse to run rather
# than flush a database this project does not own (or reset nothing, as `docker exec` would).
flush_redis() {
  (cd "$ROOT" && REDIS_URL="$REDIS_URL" uv run --package api-gateway python - <<'PY'
import os
import subprocess
import sys
import uuid

import redis

url = os.environ["REDIS_URL"]
client = redis.Redis.from_url(url)
probe = f"buvi:live-flow:probe:{uuid.uuid4()}"
client.set(probe, "1", ex=30)
seen = subprocess.run(
    ["docker", "exec", "buvi-dev-redis-1", "redis-cli", "EXISTS", probe],
    capture_output=True, text=True, check=False,
).stdout.strip()
client.delete(probe)
if seen != "1":
    sys.exit(
        f"error: {url} is not the compose Redis -- another redis-server is listening there "
        "(e.g. `brew services stop redis`). Stop it so services use buvi-dev-redis-1."
    )
client.flushdb()
PY
  )
}

PIDS=()
_stop_services() { for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; done; }
trap _stop_services EXIT

reset_demo_state() {  # [--mysql]
  "$ROOT/scripts/keycloak-bootstrap.sh" >/dev/null
  "$ROOT/scripts/migrate-all.sh" >/dev/null
  "$ROOT/scripts/seed-demo-tenant.sh" >/dev/null
  "$ROOT/scripts/seed-sample-sales.sh" >/dev/null
  if [ "${1:-}" = "--mysql" ]; then "$ROOT/scripts/seed-sample-sales-mysql.sh" >/dev/null; fi
  flush_redis
  docker exec -i "$PGCONTAINER" psql -U postgres -d agentic_bi -q -v ON_ERROR_STOP=1 <<SQL
DELETE FROM identity.mfa_credentials
  WHERE user_id IN (SELECT id FROM identity.users WHERE email = 'admin@demo.example.com');
UPDATE identity.users SET mfa_enabled = false WHERE email = 'admin@demo.example.com';
DELETE FROM metadata.data_sources WHERE name LIKE 'sample-sales-db%' OR name LIKE 'sample-sales-mysql%';
DELETE FROM identity.tenant_policies;
DELETE FROM identity.invitations WHERE email LIKE 'a10-%';
DELETE FROM identity.users WHERE email LIKE 'a10-%';
-- Demo members hold exactly their base role (A1 and A10 grant and revoke extra roles; a flow
-- that fails in between would otherwise leave them).
DELETE FROM identity.user_roles ur
  USING identity.users u, identity.roles r, (VALUES $DEMO_MEMBER_ROLES) AS m(email, role_key)
  WHERE ur.user_id = u.id AND ur.role_id = r.id AND u.email = m.email AND r.key <> m.role_key;
INSERT INTO identity.user_roles (user_id, role_id)
  SELECT u.id, r.id
  FROM (VALUES $DEMO_MEMBER_ROLES) AS m(email, role_key)
  JOIN identity.users u ON u.email = m.email
  JOIN identity.roles r ON r.tenant_id = u.tenant_id AND r.key = m.role_key
  ON CONFLICT DO NOTHING;
SQL
  assert_demo_baseline
}

# email -> the one role that demo user holds between flows
DEMO_MEMBER_ROLES="('admin@demo.example.com', 'org_admin'), ('developer@demo.example.com', 'developer'), ('client@demo.example.com', 'client')"

assert_demo_baseline() {  # fail fast if the reset left any cross-flow state behind
  local problems
  problems=$(docker exec -i "$PGCONTAINER" psql -U postgres -d agentic_bi -qAt -v ON_ERROR_STOP=1 <<SQL
SELECT 'tenant policies set' WHERE EXISTS (SELECT 1 FROM identity.tenant_policies)
UNION ALL
SELECT 'demo admin has MFA' WHERE EXISTS (
  SELECT 1 FROM identity.users u
  WHERE u.email = 'admin@demo.example.com'
    AND (u.mfa_enabled OR EXISTS (SELECT 1 FROM identity.mfa_credentials c WHERE c.user_id = u.id)))
UNION ALL
SELECT 'a10 users left' WHERE EXISTS (SELECT 1 FROM identity.users WHERE email LIKE 'a10-%')
UNION ALL
SELECT 'demo data sources left' WHERE EXISTS (
  SELECT 1 FROM metadata.data_sources
  WHERE name LIKE 'sample-sales-db%' OR name LIKE 'sample-sales-mysql%')
UNION ALL
SELECT 'roles of ' || u.email || ': ' || string_agg(r.key, ',' ORDER BY r.key)
  FROM (VALUES $DEMO_MEMBER_ROLES) AS m(email, role_key)
  JOIN identity.users u ON u.email = m.email
  JOIN identity.user_roles ur ON ur.user_id = u.id
  JOIN identity.roles r ON r.id = ur.role_id
  GROUP BY u.email, m.role_key
  HAVING array_agg(r.key) <> ARRAY[m.role_key];
SQL
  )
  if [ -n "$problems" ]; then
    echo "error: demo state not at baseline after reset:" >&2
    sed 's/^/  /' <<<"$problems" >&2
    exit 1
  fi
}

# name -> "port package module env..." (env is what the flows need beyond the defaults)
_service() {
  case "$1" in
    identity-service) echo "8001 identity_service IDENTITY_REQUIRE_GATEWAY_TOKEN=true IDENTITY_LOG_LEVEL=DEBUG" ;;
    metadata-service) echo "8002 metadata_service METADATA_REQUIRE_GATEWAY_TOKEN=true METADATA_LOG_LEVEL=DEBUG" ;;
    query-gateway) echo "8003 query_gateway QUERY_GATEWAY_LOG_LEVEL=DEBUG" ;;
    api-gateway) echo "8000 api_gateway GATEWAY_LOG_LEVEL=DEBUG" ;;
    analytics-orchestrator) echo "8004 analytics_orchestrator ANALYTICS_LOG_LEVEL=DEBUG" ;;
    worker-runtime) echo "8005 worker_runtime WORKER_LOG_LEVEL=DEBUG" ;;
    visualization-service) echo "8006 visualization_service VISUALIZATION_LOG_LEVEL=DEBUG" ;;
    dashboard-service) echo "8007 dashboard_service DASHBOARD_REQUIRE_GATEWAY_TOKEN=true DASHBOARD_LOG_LEVEL=DEBUG" ;;
    semantic-service) echo "8008 semantic_service SEMANTIC_REQUIRE_GATEWAY_TOKEN=true SEMANTIC_LOG_LEVEL=DEBUG" ;;
    # Plain HTTP to the local sample MCP server is allowed only because localhost is allow-listed
    # here (dev); staging/prod refuse loopback in this list at startup.
    mcp-gateway) echo "8009 mcp_gateway MCP_REQUIRE_GATEWAY_TOKEN=true MCP_LOG_LEVEL=DEBUG MCP_EGRESS_ALLOWED_INTERNAL_HOSTS=[\"localhost\"]" ;;
    *) echo "unknown service: $1" >&2; return 1 ;;
  esac
}

require_free_ports() {  # port...
  local port
  for port in "$@"; do
    if curl -sf "http://localhost:${port}/health/live" >/dev/null 2>&1; then
      echo "error: :${port} is already serving; stop it so this flow can capture its logs." >&2
      exit 1
    fi
  done
}

start_services() {  # name...
  local name port module env
  for name in "$@"; do
    read -r port _ <<<"$(_service "$name")"
    require_free_ports "$port"
  done
  LOGDIR="${LOGDIR:-$(mktemp -d -t buvi-flow.XXXX)}"
  export BUVI_SERVICE_LOGS="$LOGDIR"
  for name in "$@"; do
    read -r port module env <<<"$(_service "$name")"
    echo "Starting $name on :$port"
    # shellcheck disable=SC2086 # env is a list of KEY=value words; set -f: split, never glob
    (set -f; cd "$ROOT/apps/$name" && exec env $env uv run --package "$name" \
      uvicorn "$module.main:create_app" --factory --port "$port") >"$LOGDIR/$name.log" 2>&1 &
    PIDS+=("$!")
    local ready=""
    for _ in $(seq 1 60); do
      if curl -sf "http://localhost:${port}/health/live" >/dev/null 2>&1; then ready=1; break; fi
      sleep 1
    done
    if [ -z "$ready" ]; then echo "$name failed to start"; tail -40 "$LOGDIR/$name.log"; exit 1; fi
  done
  echo "Service logs: $LOGDIR"
}

run_flow() {  # script.py -- runs a flow's Python checks from the repo root
  cd "$ROOT"
  uv run --package identity-service python "scripts/$1"
}
