import { defineConfig } from "@playwright/test";

/**
 * Phase B1 DoD: a real browser, driven end to end, through the actual
 * Authorization Code + PKCE flow -- not a mock, and not the scripted
 * `httpx`-as-browser flow `scripts/test_login.py` uses (that proves the
 * backend; this proves the browser sees the right cookies and pages).
 *
 * Needs `make up` plus identity-service, api-gateway, and this app's own dev
 * server already running -- this suite does not start them itself, the same
 * division the Python live-flow scripts use (`make test-login` starts the
 * backend services it needs; this does not reach into Track A's job).
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: process.env.BUVI_APP_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
  },
  reporter: [["list"]],
});
