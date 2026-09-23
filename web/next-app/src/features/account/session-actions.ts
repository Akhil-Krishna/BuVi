"use server";

import { revalidatePath } from "next/cache";
import { callGateway, GatewayError } from "@/lib/api/gateway";

/** Mirrors `identity_service.api.v1.schemas.UserSessionResponse` (Section 6.9). */
export type UserSession = {
  id: string;
  device_label: string | null;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
};

export type RevokeOutcome = { ok: true } | { ok: false; code: string; message: string };

/** The caller's own active sessions. Scoped to the caller by identity-service
 * itself -- there is no user id in this request to tamper with (Section 6.9). */
export async function listSessions(): Promise<UserSession[]> {
  const response = await callGateway<{ items: UserSession[] }>("/api/v1/me/sessions");
  return response.items;
}

/**
 * Revoke one of the caller's own sessions. Someone else's session id is a
 * `404` from identity-service (Section 7.2's cross-resource rule), not a
 * `403` -- this surfaces whatever the server said rather than guessing.
 */
export async function revokeSession(id: string): Promise<RevokeOutcome> {
  try {
    await callGateway(`/api/v1/me/sessions/${id}`, { method: "DELETE" });
    revalidatePath("/account");
    return { ok: true };
  } catch (error) {
    if (error instanceof GatewayError) {
      return { ok: false, code: error.code, message: error.message };
    }
    return { ok: false, code: "UNKNOWN", message: "Something went wrong. Try again." };
  }
}
