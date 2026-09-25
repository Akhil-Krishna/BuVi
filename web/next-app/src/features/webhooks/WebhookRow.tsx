"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { MfaVerifyForm } from "@/features/auth/MfaVerifyForm";
import { disableWebhook } from "./actions";
import type { Webhook } from "./types";

const STATUS_CLASS: Record<string, string> = {
  active: "border-success-border bg-success-bg text-success",
  disabled: "border-border bg-bg-subtle text-text-secondary",
};

export function WebhookRow({
  webhook,
  hasWebauthn,
  hasAnyFactor,
}: {
  webhook: Webhook;
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
}) {
  const router = useRouter();
  const [pendingStepUp, setPendingStepUp] = useState(false);
  const [requireWebauthn, setRequireWebauthn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function disable() {
    setError(null);
    startTransition(async () => {
      const result = await disableWebhook(webhook.id);
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
    <div className="border-b border-border p-3 last:border-b-0" data-testid={`webhook-row-${webhook.url}`}>
      <div className="flex items-center justify-between">
        <div>
          <p className="font-mono text-sm text-text-primary">{webhook.url}</p>
          <p className="mt-0.5 text-xs text-text-secondary">{webhook.event_types.join(", ")}</p>
        </div>
        <span
          className={`rounded-sm border px-2 py-0.5 text-xs font-medium ${STATUS_CLASS[webhook.status] ?? STATUS_CLASS.disabled}`}
        >
          {webhook.status}
        </span>
      </div>

      {webhook.status === "active" && (
        <div className="mt-2">
          {pendingStepUp ? (
            <MfaVerifyForm
              hasWebauthn={hasWebauthn}
              requireWebauthn={requireWebauthn}
              onVerified={() => {
                setPendingStepUp(false);
                disable();
              }}
            />
          ) : (
            <button
              type="button"
              onClick={disable}
              disabled={isPending}
              className="rounded-sm border border-border px-3 py-1.5 text-sm text-danger hover:bg-danger hover:text-white disabled:opacity-50"
            >
              Disable
            </button>
          )}
        </div>
      )}
      {error && <p className="mt-2 text-sm text-danger">{error}</p>}
    </div>
  );
}
