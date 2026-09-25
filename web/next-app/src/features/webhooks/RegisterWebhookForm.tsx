"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { MfaVerifyForm } from "@/features/auth/MfaVerifyForm";
import { createWebhook } from "./actions";
import { WEBHOOK_EVENT_TYPES, type WebhookCreated } from "./types";

export function RegisterWebhookForm({
  hasWebauthn,
  hasAnyFactor,
  onClose,
}: {
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [eventTypes, setEventTypes] = useState<Set<string>>(new Set());
  const [pendingStepUp, setPendingStepUp] = useState(false);
  const [requireWebauthn, setRequireWebauthn] = useState(false);
  const [created, setCreated] = useState<WebhookCreated | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function submit() {
    setError(null);
    if (!url.trim() || eventTypes.size === 0) return;
    startTransition(async () => {
      const result = await createWebhook(url.trim(), [...eventTypes]);
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

  if (created) {
    return (
      <div className="border border-warning-border bg-warning-bg p-4">
        <p className="text-sm font-medium text-text-primary">
          Signing secret -- shown once, never again:
        </p>
        <p className="mt-2 break-all rounded-sm border border-border bg-bg px-3 py-2 font-mono text-sm text-text-primary">
          {created.signing_secret}
        </p>
        <button
          type="button"
          onClick={onClose}
          className="mt-3 rounded-sm bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-hover"
        >
          Done
        </button>
      </div>
    );
  }

  if (pendingStepUp) {
    return (
      <div className="max-w-sm">
        <MfaVerifyForm
          hasWebauthn={hasWebauthn}
          requireWebauthn={requireWebauthn}
          onVerified={() => {
            setPendingStepUp(false);
            submit();
          }}
        />
      </div>
    );
  }

  return (
    <div className="border border-border bg-bg p-4">
      <label className="block text-xs font-medium text-text-secondary" htmlFor="webhook-url">
        Endpoint URL
      </label>
      <input
        id="webhook-url"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="https://example.com/webhooks/buvi"
        className="mt-1 w-full rounded-sm border border-border px-2 py-1 text-sm text-text-primary"
      />

      <p className="mt-3 text-xs font-medium text-text-secondary">Event types</p>
      <div className="mt-1 flex flex-wrap gap-3">
        {WEBHOOK_EVENT_TYPES.map((type) => (
          <label key={type} className="flex items-center gap-1.5 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={eventTypes.has(type)}
              onChange={(e) => {
                const next = new Set(eventTypes);
                if (e.target.checked) next.add(type);
                else next.delete(type);
                setEventTypes(next);
              }}
            />
            {type}
          </label>
        ))}
      </div>

      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={isPending || !url.trim() || eventTypes.size === 0}
          className="rounded-sm bg-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
        >
          Register
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
    </div>
  );
}
