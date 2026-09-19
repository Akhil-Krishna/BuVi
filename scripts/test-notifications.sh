#!/usr/bin/env bash
# Phase A11 DoD scripted flow: notifications (in-app, MailHog email, signed webhooks) and billing
# usage over HTTP and the queue. Needs `make up`. Starts every service the flow crosses, with
# captured logs, then runs scripts/test_notifications.py (which is also the webhook receiver).
set -euo pipefail
source "$(dirname "$0")/live-flow.sh"

reset_demo_state
require_free_ports 8766
curl -sf -X DELETE "${MAILHOG_URL:-http://localhost:8025}/api/v1/messages" >/dev/null
start_services identity-service metadata-service query-gateway api-gateway \
  visualization-service dashboard-service semantic-service analytics-orchestrator worker-runtime \
  mcp-gateway notification-service
run_flow test_notifications.py
