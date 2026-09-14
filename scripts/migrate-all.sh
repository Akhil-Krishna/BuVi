#!/usr/bin/env bash
# Run Alembic upgrade for every service that has one (Section 27: `make migrate`).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
for ini in "$ROOT"/apps/*/alembic.ini; do
  [ -f "$ini" ] || continue
  dir="$(dirname "$ini")"; name="$(basename "$dir")"
  echo "migrate $name"
  (cd "$dir" && uv run --package "$name" alembic upgrade head)
done
