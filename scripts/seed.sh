#!/usr/bin/env bash
# Seed local dev (Section 27: `make seed`): Keycloak realm + demo users, the demo tenant,
# and the sample-sales-db customer schema.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/scripts/keycloak-bootstrap.sh"
"$ROOT/scripts/seed-demo-tenant.sh"
"$ROOT/scripts/seed-sample-sales.sh"
