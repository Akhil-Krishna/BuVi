"use server";

import { revalidatePath } from "next/cache";
import { callGateway, GatewayError } from "@/lib/api/gateway";

/** Mirrors `identity_service.api.v1.schemas` API key shapes (Section 6.8). */
export type ApiKey = {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
};

export type ApiKeyCreated = { api_key: ApiKey; secret: string; warning: string };

export type ApiKeyOutcome<T = void> =
  | ({ ok: true } & (T extends void ? Record<never, never> : { data: T }))
  | { ok: false; code: string; message: string; details: Record<string, unknown> };

function failure(error: unknown): {
  ok: false;
  code: string;
  message: string;
  details: Record<string, unknown>;
} {
  if (error instanceof GatewayError) {
    return { ok: false, code: error.code, message: error.message, details: error.details };
  }
  return { ok: false, code: "UNKNOWN", message: "Something went wrong. Try again.", details: {} };
}

export async function listApiKeys(): Promise<ApiKey[]> {
  const response = await callGateway<{ items: ApiKey[] }>("/api/v1/me/api-keys");
  return response.items;
}

/**
 * Mint an API key. Requires a fresh step-up (Section 7.3): a stolen session
 * cookie alone must not be able to create a new long-lived credential. A
 * refusal here carries `details.method` so the caller can prompt for the
 * right factor rather than guessing (Section 21).
 */
export async function createApiKey(input: {
  name: string;
  scopes: string[];
  expiresAt: string | null;
}): Promise<ApiKeyOutcome<ApiKeyCreated>> {
  try {
    const data = await callGateway<ApiKeyCreated>("/api/v1/me/api-keys", {
      method: "POST",
      body: { name: input.name, scopes: input.scopes, expires_at: input.expiresAt },
    });
    revalidatePath("/account");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

/** Revoke a key you own. Someone else's key id is a 404, not a 403 (Section 9). */
export async function revokeApiKey(id: string): Promise<ApiKeyOutcome> {
  try {
    await callGateway(`/api/v1/me/api-keys/${id}`, { method: "DELETE" });
    revalidatePath("/account");
    return { ok: true };
  } catch (error) {
    return failure(error);
  }
}
