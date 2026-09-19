#!/usr/bin/env bash
# Phase A12: the single backend suite -- the Track A exit gate. Needs the compose stack
# (`make up`), uv and Node 20. Runs, and fails on any failure:
#
#   1. contracts   every exported OpenAPI document is valid; the Section 24 registry covers the
#                  spec; static controls (no cross-schema access, no dynamic code, non-root images)
#   2. flows       every per-phase live DoD flow (the Makefile's `test-live` list, A1-A12), each
#                  from the shared self-verifying reset
#   3. system      Section 24 controls against the live stack (`pytest -m system`)
#
# All steps run even after a failure, so one run reports everything. Service logs of every flow
# are kept under $BUVI_E2E_LOGS (CI uploads it).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export BUVI_E2E_LOGS="${BUVI_E2E_LOGS:-$(mktemp -d -t buvi-e2e.XXXX)}"
mkdir -p "$BUVI_E2E_LOGS"
results=()
failed=0

step() {  # name, command...
  local name="$1"; shift
  local log="$BUVI_E2E_LOGS/$name.log"
  local started=$SECONDS
  echo "==> $name"
  if "$@" >"$log" 2>&1; then
    results+=("PASS  $name ($((SECONDS - started))s)")
  else
    results+=("FAIL  $name ($((SECONDS - started))s) -- $log")
    failed=$((failed + 1))
    tail -25 "$log"
    if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
      # A public annotation with the failure's tail: readable without repo rights to the logs.
      local tail_text
      tail_text="$(grep -E 'FAIL|Error|error|Traceback|assert' "$log" | tail -15)"
      [ -n "$tail_text" ] || tail_text="$(tail -15 "$log")"
      local pct='%' nl=$'\n' cr=$'\r'
      tail_text="${tail_text//"$pct"/%25}"
      tail_text="${tail_text//"$cr"/}"
      echo "::error title=backend-e2e: $name failed::${tail_text//"$nl"/%0A}"
    fi
  fi
}

step contracts uv run pytest tests/system -m "not system" -p no:cacheprovider

flows=$(sed -n '/^test-live:/,/^$/p' Makefile | grep -o 'scripts/test-[a-z-]*\.sh')
for flow in $flows; do
  name="$(basename "$flow" .sh)"
  mkdir -p "$BUVI_E2E_LOGS/$name"
  step "$name" env LOGDIR="$BUVI_E2E_LOGS/$name" "$flow"
done

step system env BUVI_SYSTEM=1 uv run pytest tests/system -m system -p no:cacheprovider

echo
echo "Backend e2e summary (logs: $BUVI_E2E_LOGS)"
printf '  %s\n' "${results[@]}"
if [ "$failed" -ne 0 ]; then
  echo "FAILED: $failed step(s)"
  exit 1
fi
echo "Backend e2e: all ${#results[@]} steps passed"
