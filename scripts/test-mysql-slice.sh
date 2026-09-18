#!/usr/bin/env bash
# Phase A8 DoD scripted flow: the Section 32 journey against MySQL over HTTP. Needs `make up`
# (including sample-sales-mysql). Starts every service with captured logs, then runs
# scripts/test_mysql_slice.py.
set -euo pipefail
source "$(dirname "$0")/live-flow.sh"

reset_demo_state --mysql
start_services identity-service metadata-service query-gateway api-gateway \
  visualization-service dashboard-service semantic-service analytics-orchestrator worker-runtime
run_flow test_mysql_slice.py
