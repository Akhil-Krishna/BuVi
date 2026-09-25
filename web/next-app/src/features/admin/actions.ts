"use server";

import { revalidatePath } from "next/cache";
import { callGateway, GatewayError } from "@/lib/api/gateway";
import type { AdminUser, AuditEvent, Invitation, Policies, Quotas, Usage } from "./types";

export type ActionOutcome<T = void> =
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

export async function listUsers(): Promise<AdminUser[]> {
  const response = await callGateway<{ items: AdminUser[]; next_cursor: string | null }>(
    "/api/v1/admin/users"
  );
  return response.items;
}

export async function listInvitations(): Promise<Invitation[]> {
  return callGateway<Invitation[]>("/api/v1/admin/invitations");
}

/** `user:manage` + step-up (Section 7.3): inviting someone into the tenant is
 * a privilege grant, not a plain write. */
export async function inviteUser(email: string, roleKey: string): Promise<ActionOutcome<Invitation>> {
  try {
    const data = await callGateway<Invitation>("/api/v1/admin/invitations", {
      method: "POST",
      body: { email, role_key: roleKey },
    });
    revalidatePath("/users");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

/** `role:manage` + step-up. `grant`/`revoke` are diffed client-side against
 * the user's current roles; identity-service applies both in one call and
 * refuses to leave the tenant without an `org_admin` (`LAST_ORG_ADMIN`). */
export async function changeRoles(
  userId: string,
  grant: string[],
  revoke: string[]
): Promise<ActionOutcome<{ roles: string[] }>> {
  try {
    const data = await callGateway<{ user_id: string; roles: string[] }>(
      `/api/v1/admin/users/${userId}/roles`,
      { method: "PATCH", body: { grant, revoke } }
    );
    revalidatePath("/users");
    return { ok: true, data: { roles: data.roles } };
  } catch (error) {
    return failure(error);
  }
}

/** `user:manage` + step-up: force logout, not a courtesy sign-out. */
export async function revokeUserSessions(userId: string): Promise<ActionOutcome<{ sessions_revoked: number }>> {
  try {
    const data = await callGateway<{ sessions_revoked: number }>(
      `/api/v1/admin/users/${userId}/sessions/revoke`,
      { method: "POST" }
    );
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

/** `user:manage` + step-up. Revokes every one of the target's factors and
 * sessions (Section 6.7) -- refused on your own account (`SELF_SERVICE_FORBIDDEN`). */
export async function resetUserMfa(
  userId: string
): Promise<ActionOutcome<{ factors_revoked: number; sessions_revoked: number }>> {
  try {
    const data = await callGateway<{ factors_revoked: number; sessions_revoked: number }>(
      `/api/v1/admin/users/${userId}/mfa/reset`,
      { method: "POST" }
    );
    revalidatePath("/users");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

/** `user:manage` + step-up. Deactivates, never hard-deletes; refuses to
 * remove a tenant's last `org_admin` (`LAST_ORG_ADMIN`, 409). */
export async function deactivateUser(userId: string): Promise<ActionOutcome> {
  try {
    await callGateway(`/api/v1/admin/users/${userId}`, { method: "DELETE" });
    revalidatePath("/users");
    return { ok: true };
  } catch (error) {
    return failure(error);
  }
}

export async function getPolicies(): Promise<Policies> {
  return callGateway<Policies>("/api/v1/admin/policies");
}

/** `policy:manage` + step-up. Turning on `org_admin_requires_webauthn`
 * without the acting admin holding a WebAuthn key is refused
 * (`WEBAUTHN_NOT_ENROLLED`, 409) so they cannot lock themselves out. */
export async function patchPolicies(patch: Partial<Policies>): Promise<ActionOutcome<Policies>> {
  try {
    const data = await callGateway<Policies>("/api/v1/admin/policies", {
      method: "PATCH",
      body: patch,
    });
    revalidatePath("/policies");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

export async function listAuditEvents(eventType?: string): Promise<AuditEvent[]> {
  const query = eventType ? `?event_type=${encodeURIComponent(eventType)}` : "";
  const response = await callGateway<{ items: AuditEvent[] }>(`/api/v1/admin/audit${query}`);
  return response.items;
}

export async function getUsage(): Promise<Usage> {
  return callGateway<Usage>("/api/v1/billing/usage");
}

export async function getQuotas(): Promise<Quotas> {
  return callGateway<Quotas>("/api/v1/billing/quotas");
}
