#!/usr/bin/env bash
# Phase A6 DoD scripted flow (Section 32 Steps A-D over HTTP as a client-role user). Needs
# `make up`. Starts every service with captured logs, then runs scripts/test_dashboards.py.
set -euo pipefail
source "$(dirname "$0")/live-flow.sh"

reset_demo_state
start_services identity-service metadata-service query-gateway api-gateway \
  visualization-service dashboard-service semantic-service analytics-orchestrator worker-runtime
run_flow test_dashboards.py
