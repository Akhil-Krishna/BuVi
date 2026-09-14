#!/usr/bin/env bash
# Seed local dev (Section 27: `make seed`): Keycloak realm + demo users, then the demo tenant.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/scripts/keycloak-bootstrap.sh"
"$ROOT/scripts/seed-demo-tenant.sh"
