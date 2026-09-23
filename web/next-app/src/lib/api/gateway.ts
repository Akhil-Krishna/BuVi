import "server-only";
import { cookies } from "next/headers";
import { GATEWAY_URL, SESSION_COOKIE } from "@/lib/config";

/**
 * Server-side calls to api-gateway, carrying the caller's own session cookie
 * (ADR 0018: api-gateway accepts it directly and introspects it against
 * identity-service; there is no token to attach here).
 *
 * Narrow on purpose -- this is not a general HTTP helper. It does exactly one
 * thing: issue one authenticated request to the gateway as the current user,
 * and surface the platform's own error envelope (Section 21) as a typed
 * failure rather than a thrown string.
 */

export class GatewayError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string
  ) {
    super(message);
    this.name = "GatewayError";
  }
}

type ErrorEnvelope = { error?: { code?: string; message?: string } };

export async function callGateway<T>(
  path: string,
  init: { method?: "GET" | "POST" | "DELETE" | "PATCH"; body?: unknown } = {}
): Promise<T> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  const response = await fetch(new URL(path, GATEWAY_URL), {
    method: init.method ?? "GET",
    headers: {
      "content-type": "application/json",
      ...(token ? { cookie: `${SESSION_COOKIE}=${token}` } : {}),
    },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    cache: "no-store",
  });

  if (!response.ok) {
    const envelope = (await response.json().catch(() => null)) as ErrorEnvelope | null;
    throw new GatewayError(
      response.status,
      envelope?.error?.code ?? "UNKNOWN",
      // Section 21: branch on `code`, never on `message`. The message is for a
      // human reading a log, not for control flow here or in any caller.
      envelope?.error?.message ?? `Request failed (${response.status})`
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
