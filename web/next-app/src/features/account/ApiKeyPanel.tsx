"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { MfaVerifyForm } from "@/features/auth/MfaVerifyForm";
import { createApiKey, revokeApiKey, type ApiKey, type ApiKeyCreated } from "./api-key-actions";

/**
 * API keys (Section 6.8). No Stitch screen exists for this -- follows User
 * Management's row/detail-table treatment, per Section 5.2's fallback rule,
 * and the same show-once pattern the create form uses for the secret.
 *
 * Refusals render from the platform's error `code` (Section 21), and a
 * `STEP_UP_REQUIRED` refusal is not just an error message: it is handled by
 * showing the same `MfaVerifyForm` every other step-up prompt uses, and
 * retrying the create once it succeeds.
 */
function refusalText(code: string): string {
  if (code === "RATE_LIMITED") return "Too many attempts. Wait a moment and try again.";
  return "The key could not be created.";
}

function formatWhen(value: string | null): string {
  if (!value) return "Never";
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function ApiKeyPanel({
  apiKeys,
  scopeOptions,
  hasWebauthn,
  hasAnyFactor,
}: {
  apiKeys: ApiKey[];
  /** The caller's own permissions -- a key can never exceed what its owner
   * already holds (api_key_service.py's intersection at use time), so these
   * are the only scopes worth offering. */
  scopeOptions: string[];
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
}) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<Set<string>>(new Set());
  const [pendingStepUp, setPendingStepUp] = useState(false);
  const [requireWebauthn, setRequireWebauthn] = useState(false);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function toggleScope(scope: string) {
    setScopes((current) => {
      const next = new Set(current);
      if (next.has(scope)) next.delete(scope);
      else next.add(scope);
      return next;
    });
  }

  function submitCreate() {
    setError(null);
    setPendingStepUp(false);
    startTransition(async () => {
      const result = await createApiKey({
        name,
        scopes: [...scopes],
        expiresAt: null,
      });
      if (result.ok) {
        setCreated(result.data);
        setName("");
        setScopes(new Set());
        router.refresh();
        return;
      }
      if (result.code === "STEP_UP_REQUIRED") {
        if (!hasAnyFactor) {
          setError("Add a two-factor method above, then try again.");
          return;
        }
        setRequireWebauthn(result.details.method === "webauthn");
        setPendingStepUp(true);
        return;
      }
      setError(refusalText(result.code));
    });
  }

  function revoke(key: ApiKey) {
    setError(null);
    startTransition(async () => {
      const result = await revokeApiKey(key.id);
      if (result.ok) router.refresh();
      else setError(refusalText(result.code));
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
        <p className="text-sm font-medium text-text-primary">{created.warning}</p>
        <p className="mt-3 break-all rounded-sm border border-border bg-bg px-3 py-2 font-mono text-sm text-text-primary">
          {created.secret}
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
      {apiKeys.length === 0 ? (
        <p className="text-sm text-text-secondary">No API keys.</p>
      ) : (
        <div className="overflow-x-auto border border-border">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-border bg-bg-subtle">
                <th className="px-3 py-2 text-xs font-semibold text-text-secondary">Name</th>
                <th className="px-3 py-2 text-xs font-semibold text-text-secondary">Key</th>
                <th className="px-3 py-2 text-xs font-semibold text-text-secondary">Scopes</th>
                <th className="px-3 py-2 text-xs font-semibold text-text-secondary">Last used</th>
                <th className="px-3 py-2 text-xs font-semibold text-text-secondary">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {apiKeys.map((key, index) => (
                <tr
                  key={key.id}
                  className={`border-b border-border last:border-b-0 hover:bg-row-hover ${
                    index % 2 === 1 ? "bg-bg-subtle" : "bg-bg"
                  }`}
                >
                  <td className="px-3 py-2 text-sm text-text-primary">{key.name}</td>
                  <td className="px-3 py-2 font-mono text-xs text-text-secondary">
                    {key.key_prefix}…
                  </td>
                  <td className="px-3 py-2 text-sm text-text-secondary">
                    {key.scopes.length > 0 ? key.scopes.join(", ") : "—"}
                  </td>
                  <td className="px-3 py-2 text-sm text-text-secondary">
                    {formatWhen(key.last_used_at)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => revoke(key)}
                      disabled={isPending}
                      className="rounded-sm border border-border px-2 py-1 text-xs text-danger hover:bg-danger hover:text-white disabled:opacity-50"
                    >
                      Revoke key
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-4 border border-border bg-bg p-4">
        <label className="block text-xs font-semibold text-text-secondary" htmlFor="api-key-name">
          Name
        </label>
        <input
          id="api-key-name"
          type="text"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="CI pipeline"
          className="mt-1 w-full max-w-sm rounded-sm border border-border px-3 py-2 text-sm text-text-primary"
        />

        {scopeOptions.length > 0 && (
          <fieldset className="mt-3">
            <legend className="text-xs font-semibold text-text-secondary">Scopes</legend>
            <div className="mt-1 flex flex-wrap gap-3">
              {scopeOptions.map((scope) => (
                <label key={scope} className="flex items-center gap-1.5 text-sm text-text-primary">
                  <input
                    type="checkbox"
                    checked={scopes.has(scope)}
                    onChange={() => toggleScope(scope)}
                  />
                  {scope}
                </label>
              ))}
            </div>
          </fieldset>
        )}

        <button
          type="button"
          onClick={submitCreate}
          disabled={isPending || name.trim().length === 0}
          className="mt-4 rounded-sm bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
        >
          Create key
        </button>
      </div>

      {error && <p className="mt-3 text-sm text-danger">{error}</p>}
    </div>
  );
}
