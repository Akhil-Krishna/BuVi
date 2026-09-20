"use server";

import { cookies } from "next/headers";
import { GATEWAY_URL, SESSION_COOKIE } from "@/lib/config";

/** Mirrors `identity_service.api.v1.schemas` MFA response shapes (Section 9). */
export type MfaEnrollResult = {
  method: "totp" | "webauthn";
  secret: string | null;
  provisioning_uri: string | null;
  options: Record<string, unknown> | null;
};
export type MfaChallengeResult = { options: Record<string, unknown> };
export type MfaVerifyResult =
  | { ok: true; method: "totp" | "webauthn"; step_up_expires_at: string }
  | { ok: false; code: string; message: string };

async function sessionCookieHeader(): Promise<string | null> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  return token ? `${SESSION_COOKIE}=${token}` : null;
}

async function callGateway<T>(
  path: string,
  body?: unknown,
  method: "GET" | "POST" = "POST"
): Promise<T> {
  const cookie = await sessionCookieHeader();
  const response = await fetch(new URL(path, GATEWAY_URL), {
    method,
    headers: { "content-type": "application/json", ...(cookie ? { cookie } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) {
    const problem = await response.json().catch(() => null);
    throw new Error(problem?.error?.message ?? `Request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

/** Section 6.6/7.3: the caller's enrolled factors -- never secret material. */
export type MfaFactor = {
  id: string;
  method: "totp" | "webauthn";
  label: string | null;
  confirmed_at: string | null;
};

export async function listMfaFactors(): Promise<MfaFactor[]> {
  return callGateway<MfaFactor[]>("/api/v1/me/mfa", undefined, "GET");
}

/** Section 6.6: begin TOTP or WebAuthn enrollment. */
export async function beginMfaEnrollment(method: "totp" | "webauthn"): Promise<MfaEnrollResult> {
  return callGateway<MfaEnrollResult>("/api/v1/auth/mfa/enroll", { method });
}

/** Phase A10: WebAuthn assertion options for a step-up or a login challenge. */
export async function beginMfaChallenge(): Promise<MfaChallengeResult> {
  return callGateway<MfaChallengeResult>("/api/v1/auth/mfa/challenge");
}

/** Completes enrollment or a challenge; opens the Section 7.3 step-up window. */
export async function verifyMfa(input: {
  method: "totp" | "webauthn";
  code?: string;
  credential?: Record<string, unknown>;
  label?: string;
}): Promise<MfaVerifyResult> {
  try {
    const result = await callGateway<{ method: "totp" | "webauthn"; step_up_expires_at: string }>(
      "/api/v1/auth/mfa/verify",
      input
    );
    return { ok: true, ...result };
  } catch (error) {
    return {
      ok: false,
      code: "MFA_VERIFICATION_FAILED",
      message: error instanceof Error ? error.message : "Verification failed.",
    };
  }
}
