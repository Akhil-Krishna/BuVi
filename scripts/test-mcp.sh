#!/usr/bin/env bash
# Phase A9 DoD scripted flow: MCP registration, approval, grants and invocation over HTTP. Needs
# `make up`. Starts the sample MCP server (the official SDK, on :8765) and every service the
# flow crosses, with captured logs, then runs scripts/test_mcp.py.
set -euo pipefail
source "$(dirname "$0")/live-flow.sh"

reset_demo_state
require_free_ports 8765
LOGDIR="${LOGDIR:-$(mktemp -d -t buvi-a9.XXXX)}"
echo "Starting sample MCP server on :8765"
(cd "$ROOT" && exec uv run python -m platform_testing.mcp 8765) >"$LOGDIR/sample-mcp.log" 2>&1 &
PIDS+=("$!")
for _ in $(seq 1 60); do
  curl -s -o /dev/null "http://localhost:8765/mcp" && break
  sleep 0.5
done
start_services identity-service mcp-gateway api-gateway
run_flow test_mcp.py
