"use server";

import { callGateway, GatewayError } from "@/lib/api/gateway";
import type { Snapshot } from "./types";

/** Public, token-gated (`x-auth: public`) -- an unknown, expired or revoked
 * token is a real `404` (Section 6.7: a deactivated user's share links stop
 * resolving too, since the dashboard lookup is tenant-scoped and the cascade
 * revokes them). `callGateway` sends no cookie when none exists, which is
 * exactly right for an anonymous visitor. */
export async function getSnapshot(token: string): Promise<Snapshot | null> {
  try {
    return await callGateway<Snapshot>(`/api/v1/share/${encodeURIComponent(token)}`);
  } catch (error) {
    if (error instanceof GatewayError && error.status === 404) return null;
    throw error;
  }
}
