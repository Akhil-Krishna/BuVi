#!/usr/bin/env bash
# Apply the MySQL twin of sample-sales-db and its read-only user (idempotent; Phase A8).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MYSQLCONTAINER="${MYSQLCONTAINER:-buvi-dev-sample-sales-mysql-1}"
docker exec -i "$MYSQLCONTAINER" mysql -uroot -psales --silent 2>/dev/null \
  < "$ROOT/infra/compose/sample-sales-mysql-init/01-sales-schema.sql"
echo "Seeded sample-sales-mysql: database sales, user buvi_reader"
