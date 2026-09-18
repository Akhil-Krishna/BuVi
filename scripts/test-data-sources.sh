#!/usr/bin/env bash
# Phase A3 DoD scripted flow. Needs `make up`. Starts identity, metadata and api-gateway with
# captured logs (so the flow can prove no secret was logged), then runs
# scripts/test_data_sources.py through api-gateway.
set -euo pipefail
source "$(dirname "$0")/live-flow.sh"

reset_demo_state
start_services identity-service metadata-service api-gateway
GATEWAY_URL="http://localhost:8000" METADATA_DIRECT_URL="http://localhost:8002" \
  run_flow test_data_sources.py
