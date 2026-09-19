#!/usr/bin/env bash
# Generate the typed TypeScript client from the gateway's public contract (Phase A12).
#
#   scripts/gen-client.sh           regenerate packages/ts/api-client/src/schema.d.ts, then build
#   scripts/gen-client.sh --check   fail if the committed schema drifted from the contract, and
#                                   type-check the package (CI)
#
# The source is contracts/openapi/api-gateway.json: the gateway is the only surface a browser
# reaches, and its document already composes the owning services' request/response schemas.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="$ROOT/packages/ts/api-client"
CONTRACT="$ROOT/contracts/openapi/api-gateway.json"

cd "$PKG"
[ -d node_modules ] || npm ci --no-audit --no-fund >/dev/null

if [ "${1:-}" = "--check" ]; then
  tmp="$(mktemp -t buvi-schema.XXXX).d.ts"
  npx --no-install openapi-typescript "$CONTRACT" -o "$tmp" >/dev/null
  if ! diff -q "$tmp" src/schema.d.ts >/dev/null; then
    echo "error: packages/ts/api-client/src/schema.d.ts is stale; run scripts/gen-client.sh" >&2
    diff -u src/schema.d.ts "$tmp" | head -40 >&2
    rm -f "$tmp"
    exit 1
  fi
  rm -f "$tmp"
  npx --no-install tsc -p tsconfig.json --noEmit
  echo "api-client: schema matches the contract; type-check passed"
  exit 0
fi

npx --no-install openapi-typescript "$CONTRACT" -o src/schema.d.ts >/dev/null
npx --no-install tsc -p tsconfig.json
echo "api-client: generated src/schema.d.ts and built dist/"
