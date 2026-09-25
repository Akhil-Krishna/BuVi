"use client";

import { useState } from "react";
import { RegisterWebhookForm } from "./RegisterWebhookForm";
import { WebhookRow } from "./WebhookRow";
import type { Webhook } from "./types";

/** No dedicated Stitch screen (Section 5.2) -- follows MCP Server
 * Governance's list/detail treatment, since both are "register an external
 * integration, never show its secret again" screens. */
export function WebhooksWorkspace({
  webhooks,
  hasWebauthn,
  hasAnyFactor,
}: {
  webhooks: Webhook[];
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
}) {
  const [showRegister, setShowRegister] = useState(false);

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-text-primary">Webhooks</h1>
        {!showRegister && (
          <button
            type="button"
            onClick={() => setShowRegister(true)}
            className="rounded-sm bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-hover"
          >
            Register webhook
          </button>
        )}
      </div>

      {showRegister && (
        <div className="mt-4">
          <RegisterWebhookForm
            hasWebauthn={hasWebauthn}
            hasAnyFactor={hasAnyFactor}
            onClose={() => setShowRegister(false)}
          />
        </div>
      )}

      {webhooks.length === 0 ? (
        <p className="mt-6 text-sm text-text-secondary">No webhooks registered yet.</p>
      ) : (
        <div className="mt-6 border border-border bg-bg">
          {webhooks.map((webhook) => (
            <WebhookRow
              key={webhook.id}
              webhook={webhook}
              hasWebauthn={hasWebauthn}
              hasAnyFactor={hasAnyFactor}
            />
          ))}
        </div>
      )}
    </div>
  );
}
