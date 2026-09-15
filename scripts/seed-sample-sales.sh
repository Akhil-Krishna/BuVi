#!/usr/bin/env bash
# Apply the sample-sales-db customer schema and read-only role (idempotent; Phase A3).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SALESCONTAINER="${SALESCONTAINER:-buvi-dev-sample-sales-db-1}"
docker exec -i "$SALESCONTAINER" psql -U postgres -d sample_sales -q -v ON_ERROR_STOP=1 \
  < "$ROOT/infra/compose/sample-sales-init/01-sales-schema.sql"
echo "Seeded sample-sales-db: schema sales, role buvi_reader"
