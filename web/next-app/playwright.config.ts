import { defineConfig } from "@playwright/test";

/**
 * Phase B1 DoD: a real browser, driven end to end, through the actual
 * Authorization Code + PKCE flow -- not a mock, and not the scripted
 * `httpx`-as-browser flow `scripts/test_login.py` uses (that proves the
 * backend; this proves the browser sees the right cookies and pages).
 *
 * Needs `make up` plus every backend service Phase B2's chat flow touches,
 * and this app's own dev server, already running -- this suite does not
 * start them itself, the same division the Python live-flow scripts use
 * (`make test-login` starts the backend services it needs; this does not
 * reach into Track A's job).
 *
 *   uv run --package identity-service uvicorn identity_service.main:create_app \
 *     --factory --port 8001                      # IDENTITY_REQUIRE_GATEWAY_TOKEN=true
 *   uv run --package metadata-service uvicorn metadata_service.main:create_app \
 *     --factory --port 8002
 *   uv run --package query-gateway uvicorn query_gateway.main:create_app --factory --port 8003
 *   uv run --package semantic-service uvicorn semantic_service.main:create_app \
 *     --factory --port 8008
 *   uv run --package visualization-service uvicorn visualization_service.main:create_app \
 *     --factory --port 8006
 *   uv run --package dashboard-service uvicorn dashboard_service.main:create_app \
 *     --factory --port 8007
 *   ANALYTICS_SCRIPTED_LATENCY_SECONDS=1.5 \
 *     uv run --package analytics-orchestrator uvicorn analytics_orchestrator.main:create_app \
 *     --factory --port 8004
 *   uv run --package worker-runtime uvicorn worker_runtime.main:create_app --factory --port 8005
 *   GATEWAY_RATE_AUTH_IP_CAPACITY=200 GATEWAY_RATE_AUTH_IP_REFILL_PER_SECOND=20 \
 *     uv run --package api-gateway uvicorn api_gateway.main:create_app --factory --port 8000
 *   npm run dev                                   # BUVI_GATEWAY_URL, BUVI_CALLBACK_URL
 *
 * That raised auth-tier limit is deliberate and load-shaped, not a weakening.
 * The default (10 tokens, 0.2/s -- Section 16) is tuned for a human at a login
 * form; this suite performs a dozen-plus full OIDC round trips plus MFA
 * verifications, and against the default it spends minutes queueing behind a
 * limiter rather than testing anything. The limiter keeps its production
 * defaults everywhere else and keeps its own coverage: `scripts/test_login.py`
 * asserts a burst is refused with `429` and a `Retry-After`. The helpers in
 * `e2e/sign-in.ts` still wait a limit out if they meet one, so the suite is
 * correct either way -- just slower without this.
 *
 * `ANALYTICS_SCRIPTED_LATENCY_SECONDS` (Section 23's scripted development
 * provider; `analytics_orchestrator/core/config.py`) is what gives
 * `chat-and-dashboards.spec.ts`'s cancellation test a real window to click
 * Cancel before the run finishes on its own -- at the default `0`, a scripted
 * run can complete in well under a browser round trip.
 */
export default defineConfig({
  testDir: "./e2e",
  // Generous, because a test may legitimately spend a minute waiting out the
  // auth tier's per-IP rate limit (see e2e/sign-in.ts).
  timeout: 150_000,
  retries: 0,
  // Serial, one worker, like the Python live flows: these specs share one
  // set of demo users (enrolling a factor for one changes what the next
  // sign-in must do) and one api-gateway auth-tier rate limit, which is
  // per-IP. Parallel workers would make the suite's outcome depend on
  // scheduling rather than on the behaviour under test.
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: process.env.BUVI_APP_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
  },
  reporter: [["list"]],
});
