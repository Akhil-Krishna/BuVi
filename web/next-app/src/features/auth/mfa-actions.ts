"use server";

import { revalidatePath } from "next/cache";
import { callGateway, GatewayError } from "@/lib/api/gateway";

/** Mirrors `identity_service.api.v1.schemas` MFA shapes (Sections 6.6, 9). */
export type MfaEnrollResult = {
  method: "totp" | "webauthn";
  secret: string | null;
  provisioning_uri: string | null;
  options: Record<string, unknown> | null;
};
export type MfaChallengeResult = { options: Record<string, unknown> };
export type MfaFactor = {
  id: string;
  method: "totp" | "webauthn";
  label: string | null;
  confirmed_at: string | null;
};

/** Every MFA mutation returns this shape rather than throwing, so a client
 * component can render the platform's own refusal (`STEP_UP_REQUIRED`,
 * `MFA_VERIFICATION_FAILED`, ...) by `code` -- never by message text. */
export type MfaOutcome<T = void> =
  | ({ ok: true } & (T extends void ? Record<never, never> : { data: T }))
  | { ok: false; code: string; message: string };

function failure(error: unknown): { ok: false; code: string; message: string } {
  if (error instanceof GatewayError) {
    return { ok: false, code: error.code, message: error.message };
  }
  return { ok: false, code: "UNKNOWN", message: "Something went wrong. Try again." };
}

/** Section 6.6: the first factor needs only a session; any later factor needs
 * a fresh step-up, which identity-service enforces -- this just surfaces it. */
export async function beginMfaEnrollment(
  method: "totp" | "webauthn"
): Promise<MfaOutcome<MfaEnrollResult>> {
  try {
    const data = await callGateway<MfaEnrollResult>("/api/v1/auth/mfa/enroll", {
      method: "POST",
      body: { method },
    });
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

/** Phase A10: WebAuthn assertion options for a login challenge or a step-up. */
export async function beginMfaChallenge(): Promise<MfaOutcome<MfaChallengeResult>> {
  try {
    const data = await callGateway<MfaChallengeResult>("/api/v1/auth/mfa/challenge", {
      method: "POST",
    });
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

/** Completes an enrollment or a challenge; opens the Section 7.3 step-up window. */
export async function verifyMfa(input: {
  method: "totp" | "webauthn";
  code?: string;
  credential?: Record<string, unknown>;
  label?: string;
}): Promise<MfaOutcome> {
  try {
    await callGateway("/api/v1/auth/mfa/verify", { method: "POST", body: input });
    revalidatePath("/account");
    return { ok: true };
  } catch (error) {
    return failure(error);
  }
}

export async function listMfaFactors(): Promise<MfaFactor[]> {
  return callGateway<MfaFactor[]>("/api/v1/me/mfa");
}

/** Section 7.3: removing one of your own factors is a step-up operation. A
 * caller without a fresh step-up gets `STEP_UP_REQUIRED` back, not a crash. */
export async function removeMfaFactor(id: string): Promise<MfaOutcome> {
  try {
    await callGateway(`/api/v1/me/mfa/${id}`, { method: "DELETE" });
    revalidatePath("/account");
    return { ok: true };
  } catch (error) {
    return failure(error);
  }
}
