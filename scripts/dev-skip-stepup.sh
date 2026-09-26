#!/usr/bin/env bash
# DEV ONLY. Makes every demo user's current session count as step-up-verified, so the MFA prompt
# stops appearing on credential saves, role changes, share links, webhooks, MCP approvals, etc.
#
#   scripts/dev-skip-stepup.sh          make step-up fresh
#   scripts/dev-skip-stepup.sh --undo   put it back
#
# No application code is changed or weakened. `Principal.mfa_verified` is derived as
# `session.mfa_verified_at is not None`, and `step_up_is_fresh()` tests
# `(now - mfa_verified_at) <= 5 min` -- so a timestamp in the future satisfies both, for these
# sessions only. Every real check stays exactly where it is; nothing ships different.
#
# Re-run it after signing in again: a new sign-in creates a new session row with a NULL timestamp.
set -euo pipefail
PGCONTAINER="${PGCONTAINER:-buvi-dev-postgres-1}"
SCOPE="SELECT id FROM identity.users WHERE email LIKE '%@demo.example.com'"

if [ "${1:-}" = "--undo" ]; then
  SQL="UPDATE identity.sessions SET mfa_verified_at = NULL WHERE user_id IN ($SCOPE);"
  MSG="step-up is required again"
else
  SQL="UPDATE identity.sessions SET mfa_verified_at = now() + interval '10 years' WHERE user_id IN ($SCOPE);"
  MSG="step-up satisfied for every signed-in demo user (re-run after your next sign-in)"
fi

docker exec -i "$PGCONTAINER" psql -U postgres -d agentic_bi -q -v ON_ERROR_STOP=1 -c "$SQL"
echo "$MSG"
