"use server";

import QRCode from "qrcode";
import { beginMfaEnrollment, type MfaOutcome } from "@/features/auth/mfa-actions";

export type TotpEnrollment = {
  /** For manual entry when a camera is not available. */
  secret: string;
  /** A rendered PNG data URL of the `otpauth://` URI -- generated server-side
   * so the shared secret is never handed to client JS as a scannable URI and
   * then re-encoded there. */
  qrDataUrl: string;
};

/**
 * Section 6.6: begin TOTP enrollment and render its provisioning URI as a QR.
 *
 * The secret and URI are returned exactly once by identity-service; nothing
 * here persists or logs them, and the QR is generated in the same server call
 * that receives them so the URI never needs a second round trip.
 */
export async function beginTotpEnrollment(): Promise<MfaOutcome<TotpEnrollment>> {
  const result = await beginMfaEnrollment("totp");
  if (!result.ok) return result;

  const { secret, provisioning_uri: uri } = result.data;
  if (!secret || !uri) {
    return {
      ok: false,
      code: "ENROLLMENT_INCOMPLETE",
      message: "The server did not return an enrollment secret.",
    };
  }

  const qrDataUrl = await QRCode.toDataURL(uri, { margin: 1, width: 180 });
  return { ok: true, data: { secret, qrDataUrl } };
}
