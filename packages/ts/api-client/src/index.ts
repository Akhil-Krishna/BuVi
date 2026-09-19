/**
 * Typed client for the BuVi public API (Phase A12).
 *
 * `schema.d.ts` is generated from `contracts/openapi/api-gateway.json` by `scripts/gen-client.sh`
 * and must never be edited by hand. Everything here is a thin layer over `openapi-fetch`:
 *
 * - the browser session is an HttpOnly cookie (Section 6.1), so requests send credentials and the
 *   client never sees or stores a token;
 * - every mutating request carries an `Idempotency-Key` unless the caller supplies one, so a
 *   retried request is replayed by the gateway instead of performed twice (ADR 0008);
 * - errors keep the Section 21 envelope, with helpers for the codes a UI must branch on.
 */

import createClient, { type Client, type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema.js";

export type { components, paths };
export type Schemas = components["schemas"];

/** The Section 21 error envelope every service returns. */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    request_id?: string;
    details?: Record<string, unknown>;
  };
}

export type ApiClient = Client<paths>;

export interface ApiClientOptions {
  /** The gateway origin, e.g. `https://app.example.com` (paths already start with `/api/v1`). */
  baseUrl: string;
  /** Inject a fetch implementation (tests, server-side rendering). */
  fetch?: typeof globalThis.fetch;
  /** Extra headers, e.g. a forwarded cookie on the server side. */
  headers?: Record<string, string>;
}

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

const idempotency: Middleware = {
  onRequest({ request }) {
    if (MUTATING.has(request.method) && !request.headers.has("Idempotency-Key")) {
      request.headers.set("Idempotency-Key", crypto.randomUUID());
    }
    return request;
  },
};

export function createApiClient(options: ApiClientOptions): ApiClient {
  const client = createClient<paths>({
    baseUrl: options.baseUrl.replace(/\/$/, ""),
    credentials: "include",
    ...(options.fetch ? { fetch: options.fetch } : {}),
    ...(options.headers ? { headers: options.headers } : {}),
  });
  client.use(idempotency);
  return client;
}

export function isApiError(value: unknown): value is ApiErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    "error" in value &&
    typeof (value as ApiErrorBody).error?.code === "string"
  );
}

/** Section 7.3: the operation needs a fresh MFA check; `method` says which kind. */
export function stepUpMethod(value: unknown): "webauthn" | "any" | null {
  if (!isApiError(value) || value.error.code !== "STEP_UP_REQUIRED") return null;
  return value.error.details?.["method"] === "webauthn" ? "webauthn" : "any";
}
