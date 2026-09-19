#!/usr/bin/env bash
# Phase A10 DoD scripted flow: step-up on every Section 7.3 operation, WebAuthn, tenant policies,
# share links, per-connection SQL grants and quotas, over HTTP. Needs `make up`.
set -euo pipefail
source "$(dirname "$0")/live-flow.sh"

reset_demo_state
start_services identity-service metadata-service query-gateway api-gateway \
  visualization-service dashboard-service analytics-orchestrator
run_flow test_admin.py
