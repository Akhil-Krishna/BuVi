"use client";

import { startAuthentication } from "@simplewebauthn/browser";
import { useState, useTransition, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { beginMfaChallenge, verifyMfa } from "./mfa-actions";

/**
 * Stitch reference screen: "MFA Verification" (Section 5.2). Same visual
 * pattern serves the login-time challenge here and, later, every step-up
 * re-auth prompt (B3-B7) -- Section 7.3's `STEP_UP_REQUIRED` refusal reuses
 * this exact component rather than a bespoke modal per phase.
 */
/** Renders the platform's refusal by `code`, never by message text (Section 21).
 * Being rate limited is "wait", not "wrong code" -- telling a user their code
 * was wrong when it was not is how people end up re-enrolling needlessly. */
function refusalText(code: string): string {
  if (code === "RATE_LIMITED" || code === "MFA_TOO_MANY_ATTEMPTS") {
    return "Too many attempts. Wait a moment and try again.";
  }
  return "That code was not accepted. Try again.";
}

export function MfaVerifyForm({ hasWebauthn }: { hasWebauthn: boolean }) {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function submitTotp(event: FormEvent) {
    event.preventDefault();
    setError(null);
    startTransition(async () => {
      const result = await verifyMfa({ method: "totp", code });
      if (result.ok) {
        router.replace("/");
        router.refresh();
      } else {
        setError(refusalText(result.code));
      }
    });
  }

  function submitWebauthn() {
    setError(null);
    startTransition(async () => {
      try {
        const challenge = await beginMfaChallenge();
        if (!challenge.ok) {
          setError("A security key challenge could not be started.");
          return;
        }
        const credential = await startAuthentication({
          optionsJSON: challenge.data.options as unknown as Parameters<
            typeof startAuthentication
          >[0]["optionsJSON"],
        });
        const result = await verifyMfa({
          method: "webauthn",
          credential: credential as unknown as Record<string, unknown>,
        });
        if (result.ok) {
          router.replace("/");
          router.refresh();
        } else {
          setError(refusalText(result.code));
        }
      } catch {
        setError("The security key prompt was cancelled or failed.");
      }
    });
  }

  return (
    <div className="w-full max-w-sm rounded-md border border-border bg-bg p-8">
      <h1 className="text-lg font-semibold text-text-primary">Verify your identity</h1>
      <p className="mt-1 text-sm text-text-secondary">
        Enter the 6-digit code from your authenticator app.
      </p>

      <form onSubmit={submitTotp} className="mt-6">
        <input
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={10}
          value={code}
          onChange={(event) => setCode(event.target.value)}
          className="w-full rounded-sm border border-border px-3 py-2 text-sm font-mono tracking-widest text-text-primary"
          placeholder="000000"
        />
        <button
          type="submit"
          disabled={isPending || code.length < 6}
          className="mt-3 w-full rounded-sm bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
        >
          Verify
        </button>
      </form>

      {hasWebauthn && (
        <>
          <div className="my-4 flex items-center gap-3 text-xs text-text-secondary">
            <span className="h-px flex-1 bg-border" />
            or
            <span className="h-px flex-1 bg-border" />
          </div>
          <button
            type="button"
            onClick={submitWebauthn}
            disabled={isPending}
            className="w-full rounded-sm border border-border px-4 py-2 text-sm text-text-primary hover:bg-bg-subtle disabled:opacity-50"
          >
            Use a security key
          </button>
        </>
      )}

      {error && <p className="mt-4 text-sm text-danger">{error}</p>}
    </div>
  );
}
