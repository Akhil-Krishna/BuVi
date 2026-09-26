"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { MfaVerifyForm } from "@/features/auth/MfaVerifyForm";
import { createShareLink, revokeShareLink } from "./actions";
import type { ShareLink, ShareLinkCreated } from "./types";
import { formatDateTime } from "@/lib/format";

/**
 * `dashboard:share` is a step-up operation (Section 7.3), the same pattern
 * `/account`'s API-key create flow uses (B8 note in CLAUDE.md): try the
 * action, and on `STEP_UP_REQUIRED` render the same `MfaVerifyForm` inline
 * rather than a bespoke re-auth modal.
 */
export function SharePanel({
  dashboardId,
  links,
  hasWebauthn,
  hasAnyFactor,
}: {
  dashboardId: string;
  links: ShareLink[];
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
}) {
  const router = useRouter();
  const [pendingStepUp, setPendingStepUp] = useState(false);
  const [requireWebauthn, setRequireWebauthn] = useState(false);
  const [created, setCreated] = useState<ShareLinkCreated | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function submitCreate() {
    setError(null);
    setPendingStepUp(false);
    startTransition(async () => {
      const result = await createShareLink(dashboardId, 168);
      if (result.ok) {
        setCreated(result.data);
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

  function revoke(id: string) {
    setError(null);
    startTransition(async () => {
      const result = await revokeShareLink(dashboardId, id);
      if (!result.ok) setError(result.message);
      else router.refresh();
    });
  }

  if (pendingStepUp) {
    return (
      <MfaVerifyForm
        hasWebauthn={hasWebauthn}
        requireWebauthn={requireWebauthn}
        onVerified={() => {
          setPendingStepUp(false);
          submitCreate();
        }}
      />
    );
  }

  if (created) {
    return (
      <div className="border border-warning-border bg-warning-bg p-4">
        <p className="text-sm font-medium text-text-primary">
          Copy this link now -- it will not be shown again.
        </p>
        <p className="mt-3 break-all rounded-sm border border-border bg-bg px-3 py-2 font-mono text-sm text-text-primary">
          {created.url}
        </p>
        <button
          type="button"
          onClick={() => setCreated(null)}
          className="mt-3 rounded-sm bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-hover"
        >
          Done
        </button>
      </div>
    );
  }

  return (
    <div>
      {links.length > 0 && (
        <div className="mb-3 overflow-x-auto border border-border">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-border bg-bg-subtle">
                <th className="px-3 py-2 font-semibold text-text-secondary">Status</th>
                <th className="px-3 py-2 font-semibold text-text-secondary">Expires</th>
                <th className="px-3 py-2 text-right font-semibold text-text-secondary">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {links.map((link) => (
                <tr
                  key={link.id}
                  className="border-b border-border last:border-b-0 hover:bg-row-hover"
                >
                  <td className="px-3 py-2 text-text-secondary">
                    {link.active ? "Active" : "Revoked"}
                  </td>
                  <td className="px-3 py-2 text-text-secondary">{formatDateTime(link.expires_at)}</td>
                  <td className="px-3 py-2 text-right">
                    {link.active && (
                      <button
                        type="button"
                        onClick={() => revoke(link.id)}
                        disabled={isPending}
                        className="rounded-sm border border-border px-2 py-1 text-xs text-danger hover:bg-danger hover:text-white disabled:opacity-50"
                      >
                        Revoke link
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <button
        type="button"
        onClick={submitCreate}
        disabled={isPending}
        className="rounded-sm border border-border px-3 py-1.5 text-sm text-text-primary hover:bg-bg-subtle disabled:opacity-50"
      >
        Create share link
      </button>
      {error && <p className="mt-2 text-sm text-danger">{error}</p>}
    </div>
  );
}
