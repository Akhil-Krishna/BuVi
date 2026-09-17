#!/usr/bin/env bash
# Export every service's OpenAPI document into contracts/openapi/ (Section 26).
# Owning services first, api-gateway last: the gateway composes their schemas.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/contracts/openapi}"
mkdir -p "$OUT"
export IDENTITY_LOG_LEVEL=WARNING GATEWAY_LOG_LEVEL=WARNING METADATA_LOG_LEVEL=WARNING QUERY_GATEWAY_LOG_LEVEL=WARNING ANALYTICS_LOG_LEVEL=WARNING WORKER_LOG_LEVEL=WARNING VISUALIZATION_LOG_LEVEL=WARNING DASHBOARD_LOG_LEVEL=WARNING
mains=$(ls "$ROOT"/apps/*/src/*/main.py 2>/dev/null | grep -v '/api-gateway/' || true)
[ -f "$ROOT/apps/api-gateway/src/api_gateway/main.py" ] && mains="$mains $ROOT/apps/api-gateway/src/api_gateway/main.py"
for main in $mains; do
  module="$(basename "$(dirname "$main")")"
  service="$(basename "$(dirname "$(dirname "$(dirname "$main")")")")"
  (cd "$ROOT" && uv run --package "$service" python scripts/export_openapi.py "$module" "$OUT/$service.json" >/dev/null)
  echo "exported $service"
done
