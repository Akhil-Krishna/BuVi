#!/usr/bin/env bash
# Phase A5 DoD scripted flow. Needs `make up`. Starts every service except
# analytics-orchestrator and worker-runtime, which scripts/test_analytics_run.py owns so it can
# kill and restart them mid-run.
set -euo pipefail
source "$(dirname "$0")/live-flow.sh"

reset_demo_state
require_free_ports 8004 8005
start_services identity-service metadata-service query-gateway api-gateway \
  visualization-service dashboard-service semantic-service
run_flow test_analytics_run.py
