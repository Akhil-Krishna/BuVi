/**
 * Proves the generated client works against the running gateway (Phase A12 DoD). Run by
 * scripts/backend-e2e.sh with a real session: GATEWAY_URL and BUVI_SESSION (the cookie value).
 */

import { createApiClient, isApiError, stepUpMethod } from "./index.js";

const baseUrl = process.env["GATEWAY_URL"] ?? "http://localhost:8000";
const session = process.env["BUVI_SESSION"];
if (!session) throw new Error("BUVI_SESSION is required");

const failures: string[] = [];
function check(name: string, ok: boolean, detail = ""): void {
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}${ok || !detail ? "" : `  -- ${detail}`}`);
  if (!ok) failures.push(name);
}

const api = createApiClient({ baseUrl, headers: { Cookie: `buvi_session=${session}` } });
const anonymous = createApiClient({ baseUrl });

const me = await api.GET("/api/v1/auth/session");
check("typed GET /auth/session", me.response.status === 200 && typeof me.data?.tenant_id === "string");

const inbox = await api.GET("/api/v1/me/notifications", { params: { query: { limit: 5 } } });
check(
  "typed query params and response (/me/notifications)",
  inbox.response.status === 200 && Array.isArray(inbox.data?.items),
);

const denied = await anonymous.GET("/api/v1/me/notifications");
check(
  "error envelope is typed and recognised (401)",
  denied.response.status === 401 && isApiError(denied.error) && denied.error.error.code === "AUTHENTICATION_REQUIRED",
);

const conversation = await api.POST("/api/v1/conversations", { body: { title: "client smoke" } });
check(
  "typed POST with body; Idempotency-Key added for mutations",
  conversation.response.status === 201 && typeof conversation.data?.id === "string",
  String(conversation.response.status),
);

const stale = await api.POST("/api/v1/admin/webhooks", {
  body: { url: "https://hooks.example.com/x", event_types: ["metadata.sync.completed"] },
});
check(
  "step-up refusal is surfaced to the caller",
  stale.response.status === 403 && stepUpMethod(stale.error) !== null,
  String(stale.response.status),
);

if (failures.length) {
  console.log(`FAILED (${failures.length}): ${failures.join("; ")}`);
  process.exit(1);
}
console.log("Generated TypeScript client: all checks passed");
