#!/usr/bin/env bash
# One command to run BuVi locally: infra, migrations, seed, all 11 services, and the frontend.
#
#   scripts/run-local.sh            start everything (idempotent -- safe to re-run)
#   scripts/run-local.sh --stop     stop the services and frontend (leaves Docker infra up)
#   scripts/run-local.sh --status   show what is and is not running
#   scripts/run-local.sh --down     stop everything including the Docker infra
#
# Deliberately does NOT source scripts/live-flow.sh: that file installs `trap _stop_services EXIT`,
# which kills every service the moment the calling script exits. It is right for a test flow that
# owns its services for one run, and wrong for "leave the app running so I can use it". Services
# here are started detached with nohup and outlive this script.
#
# The optional local-LLM setup is NOT part of this script -- see the end of run_report.md.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="infra/compose/docker-compose.dev.yml"
STATE_DIR="$ROOT/.run-local"
LOG_DIR="$STATE_DIR/logs"
PID_DIR="$STATE_DIR/pids"
PGCONTAINER="${PGCONTAINER:-buvi-dev-postgres-1}"

#: Every service, as "port module extra-env...". Same ports and gateway-token settings as
#: scripts/live-flow.sh's own table, so local behaviour matches what CI proves.
_service() {
  case "$1" in
    identity-service)       echo "8001 identity_service IDENTITY_REQUIRE_GATEWAY_TOKEN=true" ;;
    metadata-service)       echo "8002 metadata_service METADATA_REQUIRE_GATEWAY_TOKEN=true" ;;
    query-gateway)          echo "8003 query_gateway" ;;
    analytics-orchestrator) echo "8004 analytics_orchestrator" ;;
    worker-runtime)         echo "8005 worker_runtime" ;;
    visualization-service)  echo "8006 visualization_service" ;;
    dashboard-service)      echo "8007 dashboard_service DASHBOARD_REQUIRE_GATEWAY_TOKEN=true" ;;
    semantic-service)       echo "8008 semantic_service SEMANTIC_REQUIRE_GATEWAY_TOKEN=true" ;;
    mcp-gateway)            echo "8009 mcp_gateway MCP_REQUIRE_GATEWAY_TOKEN=true MCP_EGRESS_ALLOWED_INTERNAL_HOSTS=[\"localhost\"]" ;;
    notification-service)   echo "8010 notification_service NOTIFICATION_REQUIRE_GATEWAY_TOKEN=true NOTIFICATION_EGRESS_ALLOWED_INTERNAL_HOSTS=[\"localhost\"]" ;;
    api-gateway)            echo "8000 api_gateway" ;;
    *) echo "unknown service: $1" >&2; return 1 ;;
  esac
}

#: api-gateway last: it is the front door, so it comes up once its upstreams already answer.
SERVICES="identity-service metadata-service query-gateway semantic-service visualization-service
dashboard-service analytics-orchestrator worker-runtime mcp-gateway notification-service api-gateway"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
die()  { printf '\n\033[31mERROR\033[0m %s\n\n' "$1" >&2; exit 1; }

# --- preflight ---------------------------------------------------------------------------------

preflight() {
  bold "Checking prerequisites"
  local missing=""
  command -v docker >/dev/null 2>&1 || missing="$missing\n  - docker      https://www.docker.com/products/docker-desktop/"
  command -v uv     >/dev/null 2>&1 || missing="$missing\n  - uv          https://docs.astral.sh/uv/getting-started/installation/"
  command -v node   >/dev/null 2>&1 || missing="$missing\n  - node (20+)  https://nodejs.org/"
  command -v npm    >/dev/null 2>&1 || missing="$missing\n  - npm         ships with Node.js"
  if [ -n "$missing" ]; then
    die "Missing required tool(s):$(printf '%b' "$missing")

Install the above, then re-run: scripts/run-local.sh"
  fi
  ok "docker, uv, node, npm found"

  docker info >/dev/null 2>&1 || die "Docker is installed but the daemon is not running.
Start Docker Desktop, wait for it to say \"Engine running\", then re-run: scripts/run-local.sh"
  ok "Docker daemon is running"

  local major
  major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  if [ "$major" -lt 20 ]; then
    die "Node 20+ is required; found $(node --version). Upgrade Node, then re-run."
  fi
  ok "Node $(node --version)"
}

# --- infra ------------------------------------------------------------------------------------

wait_for_containers() {
  bold "Waiting for infrastructure containers"
  local names deadline
  names="$(docker compose -f "$COMPOSE_FILE" ps --format '{{.Name}}')"
  [ -n "$names" ] || die "No containers came up. Check: docker compose -f $COMPOSE_FILE logs"
  deadline=$(( $(date +%s) + 180 ))
  for name in $names; do
    # mailhog and the OTel collector declare no healthcheck, so "running" is all there is to
    # wait for -- blanket-waiting for `healthy` would hang on them forever.
    local has_hc
    has_hc="$(docker inspect --format '{{if .Config.Healthcheck}}yes{{else}}no{{end}}' "$name" 2>/dev/null || echo no)"
    while :; do
      local state health
      state="$(docker inspect --format '{{.State.Status}}' "$name" 2>/dev/null || echo missing)"
      health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name" 2>/dev/null || echo none)"
      if [ "$has_hc" = "yes" ] && [ "$health" = "healthy" ]; then ok "$name (healthy)"; break; fi
      if [ "$has_hc" = "no" ] && [ "$state" = "running" ]; then ok "$name (running, no healthcheck)"; break; fi
      if [ "$state" = "exited" ] || [ "$state" = "dead" ]; then
        die "$name exited. Logs: docker compose -f $COMPOSE_FILE logs $name"
      fi
      if [ "$(date +%s)" -ge "$deadline" ]; then
        die "$name did not become ready within 180s (status=$state health=$health).
Logs: docker compose -f $COMPOSE_FILE logs $name"
      fi
      sleep 2
    done
  done
}

psql_q() { # sql -> stdout, empty on any failure
  docker exec -i "$PGCONTAINER" psql -U postgres -d agentic_bi -tA -c "$1" 2>/dev/null || true
}

initialize_data() {
  # Both checks read the database rather than a marker file: a sentinel can be stale or lie after
  # a `docker volume rm`, the database cannot.
  bold "Database migrations and seed data"

  if [ "$(psql_q "SELECT to_regclass('identity.users') IS NOT NULL")" != "t" ]; then
    warn "schema not present -- running make migrate (first run, takes a minute)"
    make migrate >"$LOG_DIR/migrate.log" 2>&1 \
      || die "make migrate failed. Log: $LOG_DIR/migrate.log"
    ok "migrations applied"
  else
    ok "migrations already applied"
  fi

  if [ "$(psql_q "SELECT 1 FROM identity.users WHERE email = 'admin@demo.example.com'")" != "1" ]; then
    warn "demo tenant not present -- running make seed"
    make seed >"$LOG_DIR/seed.log" 2>&1 \
      || die "make seed failed. Log: $LOG_DIR/seed.log"
    ok "demo tenant, demo users and sample-sales-db seeded"
  else
    ok "demo tenant already seeded"
  fi
}

# --- services ---------------------------------------------------------------------------------

healthy() { curl -sf "http://localhost:$1/health/ready" >/dev/null 2>&1; }

start_backend() {
  bold "Starting the 11 backend services"
  # A human clicking Sign In a few times would otherwise spend the auth-tier limiter's whole
  # budget (Section 16 sizes it for one person at one login form).
  export GATEWAY_RATE_AUTH_IP_CAPACITY="${GATEWAY_RATE_AUTH_IP_CAPACITY:-200}"
  export GATEWAY_RATE_AUTH_IP_REFILL_PER_SECOND="${GATEWAY_RATE_AUTH_IP_REFILL_PER_SECOND:-20}"

  for name in $SERVICES; do
    local spec port module env_pairs
    spec="$(_service "$name")"
    port="$(echo "$spec" | cut -d' ' -f1)"
    module="$(echo "$spec" | cut -d' ' -f2)"
    env_pairs="$(echo "$spec" | cut -s -d' ' -f3-)"

    if healthy "$port"; then ok "$name already running on :$port"; continue; fi
    if lsof -ti:"$port" >/dev/null 2>&1; then
      die "Port $port is taken by something that is not a healthy $name.
Free it, then re-run:  lsof -ti:$port | xargs kill"
    fi

    # shellcheck disable=SC2086 # env_pairs is intentionally word-split into KEY=value args
    ( cd "apps/$name" \
      && exec env $env_pairs nohup uv run --package "$name" \
           uvicorn "$module.main:create_app" --factory --host 127.0.0.1 --port "$port" \
    ) >"$LOG_DIR/$name.log" 2>&1 &
    echo $! >"$PID_DIR/$name.pid"

    local deadline; deadline=$(( $(date +%s) + 90 ))
    while ! healthy "$port"; do
      if [ "$(date +%s)" -ge "$deadline" ]; then
        printf '\n'; tail -25 "$LOG_DIR/$name.log" >&2
        die "$name did not become ready on :$port within 90s. Full log: $LOG_DIR/$name.log"
      fi
      sleep 1
    done
    ok "$name on :$port"
  done
}

start_frontend() {
  bold "Starting the frontend"
  if curl -sf http://localhost:3000/login >/dev/null 2>&1; then
    ok "already running on :3000"; return
  fi
  if lsof -ti:3000 >/dev/null 2>&1; then
    die "Port 3000 is taken but not serving BuVi. Free it:  lsof -ti:3000 | xargs kill"
  fi
  if [ ! -d web/next-app/node_modules ]; then
    warn "installing npm dependencies (first run, takes a minute)"
    ( cd web/next-app && npm install ) >"$LOG_DIR/npm-install.log" 2>&1 \
      || die "npm install failed. Log: $LOG_DIR/npm-install.log"
    ok "npm dependencies installed"
  fi
  ( cd web/next-app && exec nohup npm run dev ) >"$LOG_DIR/frontend.log" 2>&1 &
  echo $! >"$PID_DIR/frontend.pid"

  local deadline; deadline=$(( $(date +%s) + 120 ))
  while ! curl -sf http://localhost:3000/login >/dev/null 2>&1; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      printf '\n'; tail -25 "$LOG_DIR/frontend.log" >&2
      die "The frontend did not start within 120s. Full log: $LOG_DIR/frontend.log"
    fi
    sleep 2
  done
  ok "frontend on :3000"
}

# --- commands ---------------------------------------------------------------------------------

do_start() {
  mkdir -p "$LOG_DIR" "$PID_DIR"
  preflight
  bold "Starting infrastructure (docker compose)"
  make up >"$LOG_DIR/compose-up.log" 2>&1 || die "make up failed. Log: $LOG_DIR/compose-up.log"
  ok "compose up issued"
  wait_for_containers
  initialize_data
  start_backend
  start_frontend

  cat <<EOF

$(bold "BuVi is running.")

  Open:  http://localhost:3000

  Sign in with any of these (all use password: Demo-Passw0rd!23)

    demo-client      client@demo.example.com      Chat, dashboards
    demo-developer   developer@demo.example.com   + Data Sources, SQL Lab, Semantic, MCP
    demo-admin       admin@demo.example.com       + Users, Policies, Audit, Billing, Webhooks

  Other UIs:  Keycloak http://localhost:8080 (admin/admin) · MailHog http://localhost:8025

  Logs:       $LOG_DIR
  Status:     scripts/run-local.sh --status

$(bold "To stop everything:")

  scripts/run-local.sh --stop     # services + frontend (Docker infra keeps running)
  scripts/run-local.sh --down     # the above, plus the Docker infra

EOF
}

do_stop() {
  bold "Stopping services and frontend"
  local stopped=0
  if [ -d "$PID_DIR" ]; then
    for pidfile in "$PID_DIR"/*.pid; do
      [ -e "$pidfile" ] || continue
      local name pid; name="$(basename "$pidfile" .pid)"; pid="$(cat "$pidfile")"
      if kill "$pid" 2>/dev/null; then ok "stopped $name (pid $pid)"; stopped=$((stopped+1)); fi
      rm -f "$pidfile"
    done
  fi
  # uvicorn/next spawn children that outlive the pid we recorded, so sweep the ports too.
  local leftovers
  leftovers="$(lsof -ti:3000,8000,8001,8002,8003,8004,8005,8006,8007,8008,8009,8010 2>/dev/null || true)"
  if [ -n "$leftovers" ]; then
    echo "$leftovers" | xargs kill 2>/dev/null || true
    sleep 1
    leftovers="$(lsof -ti:3000,8000,8001,8002,8003,8004,8005,8006,8007,8008,8009,8010 2>/dev/null || true)"
    [ -n "$leftovers" ] && echo "$leftovers" | xargs kill -9 2>/dev/null || true
    ok "freed application ports"
    stopped=$((stopped+1))
  fi
  [ "$stopped" -eq 0 ] && warn "nothing was running"
  echo
}

do_status() {
  bold "Infrastructure"
  docker compose -f "$COMPOSE_FILE" ps --format '  {{.Name}}\t{{.Status}}' 2>/dev/null \
    || warn "docker compose not reachable"
  echo
  bold "Services"
  for name in $SERVICES; do
    local port; port="$(_service "$name" | cut -d' ' -f1)"
    if healthy "$port"; then ok "$(printf '%-24s :%s' "$name" "$port")"
    else warn "$(printf '%-24s :%s  DOWN' "$name" "$port")"; fi
  done
  if curl -sf http://localhost:3000/login >/dev/null 2>&1; then ok "$(printf '%-24s :3000' frontend)"
  else warn "$(printf '%-24s :3000  DOWN' frontend)"; fi
  echo
}

case "${1:-start}" in
  start|"")  do_start ;;
  --stop)    do_stop ;;
  --down)    do_stop; bold "Stopping Docker infrastructure"; make down; echo ;;
  --status)  do_status ;;
  -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) die "Unknown option: $1  (try: start | --stop | --down | --status | --help)" ;;
esac
