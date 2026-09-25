"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { MfaVerifyForm } from "@/features/auth/MfaVerifyForm";
import { inviteUser } from "./actions";
import { TENANT_ROLES } from "./types";

export function InviteForm({
  hasWebauthn,
  hasAnyFactor,
  onClose,
}: {
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [roleKey, setRoleKey] = useState<string>("developer");
  const [pendingStepUp, setPendingStepUp] = useState(false);
  const [requireWebauthn, setRequireWebauthn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function submit() {
    setError(null);
    if (!email.trim()) return;
    startTransition(async () => {
      const result = await inviteUser(email.trim(), roleKey);
      if (result.ok) {
        router.refresh();
        onClose();
        return;
      }
      if (result.code === "STEP_UP_REQUIRED") {
        if (!hasAnyFactor) {
          setError("Add a two-factor method in Account settings, then try again.");
          return;
        }
        setRequireWebauthn(result.details.method === "webauthn");
        setPendingStepUp(true);
        return;
      }
      setError(result.message);
    });
  }

  return (
    <div className="border border-border bg-bg p-4">
      {pendingStepUp ? (
        <MfaVerifyForm hasWebauthn={hasWebauthn} requireWebauthn={requireWebauthn} onVerified={submit} />
      ) : (
        <>
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label className="block text-xs font-medium text-text-secondary" htmlFor="invite-email">
                Email
              </label>
              <input
                id="invite-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 rounded-sm border border-border px-2 py-1 text-sm text-text-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-text-secondary" htmlFor="invite-role">
                Role
              </label>
              <select
                id="invite-role"
                value={roleKey}
                onChange={(e) => setRoleKey(e.target.value)}
                className="mt-1 rounded-sm border border-border px-2 py-1 text-sm text-text-primary"
              >
                {TENANT_ROLES.map((role) => (
                  <option key={role} value={role}>
                    {role}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              onClick={submit}
              disabled={isPending || !email.trim()}
              className="rounded-sm bg-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
            >
              Send invitation
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-sm border border-border px-3 py-1.5 text-sm text-text-primary hover:bg-bg-subtle"
            >
              Cancel
            </button>
          </div>
          {error && <p className="mt-2 text-sm text-danger">{error}</p>}
        </>
      )}
    </div>
  );
}
