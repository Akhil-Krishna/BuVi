#!/usr/bin/env bash
# Phase A4 DoD scripted flow. Needs `make up`. Starts identity, metadata, query-gateway and
# api-gateway with captured logs, then runs scripts/test_query_gateway.py.
set -euo pipefail
source "$(dirname "$0")/live-flow.sh"

reset_demo_state
start_services identity-service metadata-service query-gateway api-gateway
run_flow test_query_gateway.py
