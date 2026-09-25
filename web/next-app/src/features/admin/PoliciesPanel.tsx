"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { MfaVerifyForm } from "@/features/auth/MfaVerifyForm";
import { patchPolicies } from "./actions";
import type { Policies } from "./types";

const LABELS: Record<keyof Policies, string> = {
  client_can_share_dashboards: "Clients can create dashboard share links",
  developer_can_manage_mcp: "Developers can manage MCP servers",
  org_admin_requires_webauthn: "Org admins must use a security key for step-up",
};

/** No dedicated Stitch screen (Section 5.2) -- follows User Management's
 * row/panel treatment. */
export function PoliciesPanel({
  policies,
  hasWebauthn,
  hasAnyFactor,
}: {
  policies: Policies;
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
}) {
  const router = useRouter();
  const [draft, setDraft] = useState(policies);
  const [savedFor, setSavedFor] = useState(policies);
  if (savedFor !== policies) {
    setSavedFor(policies);
    setDraft(policies);
  }
  const [pendingStepUp, setPendingStepUp] = useState(false);
  const [requireWebauthn, setRequireWebauthn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const dirty = (Object.keys(LABELS) as (keyof Policies)[]).some((k) => draft[k] !== policies[k]);

  function save() {
    setError(null);
    const patch = (Object.keys(LABELS) as (keyof Policies)[]).reduce<Partial<Policies>>((acc, k) => {
      if (draft[k] !== policies[k]) acc[k] = draft[k];
      return acc;
    }, {});
    startTransition(async () => {
      const result = await patchPolicies(patch);
      if (result.ok) {
        setPendingStepUp(false);
        router.refresh();
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
    <div>
      <h1 className="text-xl font-semibold text-text-primary">Tenant Policies</h1>
      <div className="mt-6 max-w-xl border border-border bg-bg p-4">
        {(Object.keys(LABELS) as (keyof Policies)[]).map((key) => (
          <label key={key} className="flex items-center gap-2 py-2 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={draft[key]}
              onChange={(e) => setDraft({ ...draft, [key]: e.target.checked })}
            />
            {LABELS[key]}
          </label>
        ))}

        {pendingStepUp ? (
          <div className="mt-3">
            <MfaVerifyForm hasWebauthn={hasWebauthn} requireWebauthn={requireWebauthn} onVerified={save} />
          </div>
        ) : (
          <button
            type="button"
            onClick={save}
            disabled={isPending || !dirty}
            className="mt-3 rounded-sm bg-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
          >
            Save changes
          </button>
        )}
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
      </div>
    </div>
  );
}
