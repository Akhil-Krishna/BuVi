import { execFileSync } from "node:child_process";

/**
 * Put one demo user back to "no MFA factors, no live sessions, not locked out".
 *
 * Every Python live flow starts with `reset_demo_state` so that "each resets
 * demo state, so order does not matter" (`make test-live`). These specs enrol
 * factors and deliberately submit one wrong code, so they need the same
 * property: without it, whichever spec ran first would decide whether the next
 * one's sign-in lands on the app or on the MFA gate, and running a single spec
 * alone would behave differently again.
 *
 * The audit rows matter as much as the factors. `MfaService._throttle` counts
 * `auth.mfa_verification_failed` events over a 15-minute window
 * (`mfa_failure_window_seconds`) and locks the account past
 * `mfa_max_failures` -- a real control, deliberately durable so it survives
 * restarts and spans replicas. Those rows *are* the lockout state, so a reset
 * that leaves them behind locks the account out of the next few runs. Only
 * the demo users' own MFA-failure rows are removed; nothing else in the audit
 * trail is touched.
 *
 * Users and roles are never touched -- they come from `scripts/test-login.sh`'s
 * invitation flow and are expensive to rebuild.
 */
const PG_CONTAINER = process.env.PGCONTAINER ?? "buvi-dev-postgres-1";

export function clearMfaAndSessions(...emails: string[]): void {
  const list = emails.map((email) => `'${email.replace(/'/g, "''")}'`).join(", ");
  const scope = `SELECT id FROM identity.users WHERE email IN (${list})`;
  const sql = `
    DELETE FROM identity.mfa_credentials WHERE user_id IN (${scope});
    DELETE FROM identity.sessions WHERE user_id IN (${scope});
    DELETE FROM identity.audit_events
     WHERE event_type = 'auth.mfa_verification_failed'
       AND actor_user_id IN (${scope});
    UPDATE identity.users SET mfa_enabled = false WHERE id IN (${scope});
  `;
  execFileSync(
    "docker",
    [
      "exec",
      "-i",
      PG_CONTAINER,
      "psql",
      "-U",
      "postgres",
      "-d",
      "agentic_bi",
      "-q",
      "-v",
      "ON_ERROR_STOP=1",
    ],
    { input: sql, stdio: ["pipe", "ignore", "inherit"] }
  );
}

export const DEMO = {
  client: "client@demo.example.com",
  developer: "developer@demo.example.com",
  admin: "admin@demo.example.com",
} as const;
