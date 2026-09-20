import "server-only";
import { cookies } from "next/headers";
import { GATEWAY_URL, SESSION_COOKIE } from "@/lib/config";

/** Mirrors `identity_service.api.v1.schemas.SessionResponse` (Section 9). */
export type Session = {
  user_id: string;
  tenant_id: string;
  email: string;
  display_name: string;
  roles: string[];
  permissions: string[];
  auth_method: string;
  mfa_enabled: boolean;
  mfa_verified: boolean;
  step_up_fresh: boolean;
  step_up_method: "webauthn" | null;
  session_id: string | null;
  expires_at: string | null;
};

/**
 * Reads the caller's session from api-gateway (`GET /auth/session`), server
 * side, using the browser's own session cookie. Returns `null` for anything
 * other than a clean `200` -- an expired/revoked/garbage cookie is not this
 * function's problem to classify, only `proxy.ts`'s and each layout's
 * decision about where to send the visitor next.
 *
 * This is a real network call, not a cookie decode -- proxy.ts deliberately
 * does not call it (Section 4.2), but a layout rendering role-scoped nav has
 * to fetch the session to render at all, so the cost is not extra here.
 */
export async function getSession(): Promise<Session | null> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) return null;

  const response = await fetch(new URL("/api/v1/auth/session", GATEWAY_URL), {
    headers: { cookie: `${SESSION_COOKIE}=${token}` },
    cache: "no-store",
  });
  if (!response.ok) return null;
  return (await response.json()) as Session;
}
