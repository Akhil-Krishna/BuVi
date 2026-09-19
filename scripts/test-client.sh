#!/usr/bin/env bash
# Phase A12 DoD: the generated TypeScript client drives the running gateway. Needs `make up` and
# Node 20. Checks the client matches the contract, builds it, logs in the demo admin, and runs
# packages/ts/api-client/src/smoke.ts against identity, orchestrator and notification routes.
set -euo pipefail
source "$(dirname "$0")/live-flow.sh"

reset_demo_state
"$ROOT/scripts/gen-client.sh" --check
(cd "$ROOT/packages/ts/api-client" && npx --no-install tsc -p tsconfig.json)
start_services identity-service api-gateway analytics-orchestrator notification-service
session=$(cd "$ROOT" && uv run --package identity-service python - <<'PY'
import sys

sys.path.insert(0, "scripts")
from test_login import USERS, login

print(login(USERS["org_admin"][0])[0])
PY
)
GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}" BUVI_SESSION="$session" \
  node "$ROOT/packages/ts/api-client/dist/smoke.js"
