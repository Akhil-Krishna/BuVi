#!/usr/bin/env bash
# Phase A7 DoD scripted flow: semantic metrics over HTTP and their use by the chat flow. Needs
# `make up`. Starts every service with captured logs, then runs scripts/test_semantics.py.
set -euo pipefail
source "$(dirname "$0")/live-flow.sh"

reset_demo_state
start_services identity-service metadata-service query-gateway api-gateway \
  visualization-service dashboard-service semantic-service analytics-orchestrator worker-runtime
run_flow test_semantics.py
