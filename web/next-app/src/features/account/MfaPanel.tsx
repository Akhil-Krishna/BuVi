"use client";

import Image from "next/image";
import { startRegistration } from "@simplewebauthn/browser";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import {
  beginMfaEnrollment,
  removeMfaFactor,
  verifyMfa,
  type MfaFactor,
} from "@/features/auth/mfa-actions";
import { beginTotpEnrollment, type TotpEnrollment } from "./enroll-actions";
import { formatDate } from "@/lib/format";

/**
 * Enrollment and factor management (Section 6.6). Verification lives in
 * `features/auth/MfaVerifyForm` -- that one matches the Stitch "MFA
 * Verification" screen; this follows the same panel treatment for the
 * enrollment side, which has no Stitch screen of its own (Section 5.2).
 *
 * Refusals are rendered from the platform's error `code` (Section 21), so a
 * `STEP_UP_REQUIRED` on a second factor reads as an instruction rather than
 * a generic failure.
 */
function refusalText(code: string, fallback: string): string {
  if (code === "STEP_UP_REQUIRED") {
    return "Verify an existing factor first, then try again.";
  }
  if (code === "RATE_LIMITED" || code === "MFA_TOO_MANY_ATTEMPTS") {
    return "Too many attempts. Wait a moment and try again.";
  }
  if (code === "MFA_VERIFICATION_FAILED") {
    return "That code was not accepted. Check your authenticator app and try again.";
  }
  return fallback;
}

export function MfaPanel({ factors }: { factors: MfaFactor[] }) {
  const router = useRouter();
  const [totp, setTotp] = useState<TotpEnrollment | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function reset() {
    setError(null);
    setNotice(null);
  }

  function startTotp() {
    reset();
    startTransition(async () => {
      const result = await beginTotpEnrollment();
      if (result.ok) setTotp(result.data);
      else setError(refusalText(result.code, result.message));
    });
  }

  function confirmTotp() {
    reset();
    startTransition(async () => {
      const result = await verifyMfa({ method: "totp", code });
      if (result.ok) {
        setTotp(null);
        setCode("");
        setNotice("Authenticator app added.");
        router.refresh();
      } else {
        setError(refusalText(result.code, result.message));
      }
    });
  }

  function enrollWebauthn() {
    reset();
    startTransition(async () => {
      const started = await beginMfaEnrollment("webauthn");
      if (!started.ok) {
        setError(refusalText(started.code, started.message));
        return;
      }
      if (!started.data.options) {
        setError("The server did not return a registration challenge.");
        return;
      }
      try {
        const credential = await startRegistration({
          optionsJSON: started.data.options as unknown as Parameters<
            typeof startRegistration
          >[0]["optionsJSON"],
        });
        const verified = await verifyMfa({
          method: "webauthn",
          credential: credential as unknown as Record<string, unknown>,
          label: "Security key",
        });
        if (verified.ok) {
          setNotice("Security key added.");
          router.refresh();
        } else {
          setError(refusalText(verified.code, verified.message));
        }
      } catch {
        setError("The security key prompt was cancelled or failed.");
      }
    });
  }

  function remove(factor: MfaFactor) {
    reset();
    startTransition(async () => {
      const result = await removeMfaFactor(factor.id);
      if (result.ok) {
        setNotice("Factor removed.");
        router.refresh();
      } else {
        setError(refusalText(result.code, result.message));
      }
    });
  }

  return (
    <div>
      {factors.length === 0 ? (
        <p className="text-sm text-text-secondary">
          No factors enrolled. Add one to protect sensitive actions.
        </p>
      ) : (
        <div className="overflow-x-auto border border-border">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-border bg-bg-subtle">
                <th className="px-3 py-2 text-xs font-semibold text-text-secondary">Method</th>
                <th className="px-3 py-2 text-xs font-semibold text-text-secondary">Label</th>
                <th className="px-3 py-2 text-xs font-semibold text-text-secondary">Added</th>
                <th className="px-3 py-2 text-xs font-semibold text-text-secondary">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {factors.map((factor, index) => (
                <tr
                  key={factor.id}
                  className={`border-b border-border last:border-b-0 hover:bg-row-hover ${
                    index % 2 === 1 ? "bg-bg-subtle" : "bg-bg"
                  }`}
                >
                  <td className="px-3 py-2 text-sm text-text-primary">
                    {factor.method === "totp" ? "Authenticator app" : "Security key"}
                  </td>
                  <td className="px-3 py-2 text-sm text-text-secondary">{factor.label ?? "—"}</td>
                  <td className="px-3 py-2 text-sm text-text-secondary">
                    {factor.confirmed_at ? formatDate(factor.confirmed_at) : "Pending"}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => remove(factor)}
                      disabled={isPending}
                      className="rounded-sm border border-border px-2 py-1 text-xs text-danger hover:bg-danger hover:text-white disabled:opacity-50"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {totp ? (
        <div className="mt-4 border border-border bg-bg p-4">
          <p className="text-sm font-medium text-text-primary">
            Scan this with your authenticator app
          </p>
          <Image
            src={totp.qrDataUrl}
            alt="TOTP enrollment QR code"
            width={180}
            height={180}
            unoptimized
            className="mt-3 border border-border"
          />
          <p className="mt-3 text-xs text-text-secondary">
            Or enter this key manually:{" "}
            <span className="font-mono text-text-primary">{totp.secret}</span>
          </p>
          <div className="mt-3 flex gap-2">
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={10}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              placeholder="000000"
              aria-label="Verification code"
              className="w-32 rounded-sm border border-border px-3 py-2 font-mono text-sm tracking-widest text-text-primary"
            />
            <button
              type="button"
              onClick={confirmTotp}
              disabled={isPending || code.length < 6}
              className="rounded-sm bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
            >
              Confirm
            </button>
            <button
              type="button"
              onClick={() => {
                setTotp(null);
                setCode("");
                reset();
              }}
              className="rounded-sm border border-border px-4 py-2 text-sm text-text-primary hover:bg-bg-subtle"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-4 flex gap-2">
          <button
            type="button"
            onClick={startTotp}
            disabled={isPending}
            className="rounded-sm border border-border px-3 py-2 text-sm text-text-primary hover:bg-bg-subtle disabled:opacity-50"
          >
            Add authenticator app
          </button>
          <button
            type="button"
            onClick={enrollWebauthn}
            disabled={isPending}
            className="rounded-sm border border-border px-3 py-2 text-sm text-text-primary hover:bg-bg-subtle disabled:opacity-50"
          >
            Add security key
          </button>
        </div>
      )}

      {notice && <p className="mt-3 text-sm text-success">{notice}</p>}
      {error && <p className="mt-3 text-sm text-danger">{error}</p>}
    </div>
  );
}
