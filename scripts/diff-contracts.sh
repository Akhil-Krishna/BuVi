#!/usr/bin/env bash
# Regenerate contracts into a temp dir and compare with contracts/openapi/ (Section 26).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
"$ROOT/scripts/gen-openapi.sh" "$TMP" >/dev/null
uv run python "$ROOT/scripts/openapi_diff.py" "$ROOT/contracts/openapi" "$TMP"
