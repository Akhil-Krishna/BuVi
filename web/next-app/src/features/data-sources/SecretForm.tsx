"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { MfaVerifyForm } from "@/features/auth/MfaVerifyForm";
import { setDataSourceSecret } from "./actions";

const DEFAULT_PORTS: Record<string, number> = { postgres: 5432, mysql: 3306, redshift: 5439 };

/**
 * `POST .../secret` is step-up protected (Section 7.3, 13.1): a stolen
 * session cookie alone must not be able to point a data source at an
 * attacker-controlled host. Same inline retry pattern as API keys/share
 * links -- try the submit, and on `STEP_UP_REQUIRED` render the same
 * `MfaVerifyForm` in place, retrying once it succeeds. The credential itself
 * is never echoed back in any response, so this form has nothing to
 * pre-fill even when updating an already-active connection.
 */
export function SecretForm({
  dataSourceId,
  engine,
  hasWebauthn,
  hasAnyFactor,
  onDone,
}: {
  dataSourceId: string;
  engine: string;
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
  onDone: () => void;
}) {
  const router = useRouter();
  const [host, setHost] = useState("");
  const [port, setPort] = useState(DEFAULT_PORTS[engine] ?? 5432);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [sslmode, setSslmode] = useState("require");
  const [pendingStepUp, setPendingStepUp] = useState(false);
  const [requireWebauthn, setRequireWebauthn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function submit() {
    if (!host.trim() || !username.trim() || !password) {
      setError("Host, username, and password are required.");
      return;
    }
    setError(null);
    setPendingStepUp(false);
    startTransition(async () => {
      const result = await setDataSourceSecret(dataSourceId, {
        host: host.trim(),
        port,
        username: username.trim(),
        password,
        sslmode,
      });
      if (result.ok) {
        router.refresh();
        onDone();
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

  if (pendingStepUp) {
    return (
      <MfaVerifyForm
        hasWebauthn={hasWebauthn}
        requireWebauthn={requireWebauthn}
        onVerified={() => {
          setPendingStepUp(false);
          submit();
        }}
      />
    );
  }

  return (
    <div className="border border-border bg-bg-subtle p-4">
      <p className="text-sm font-medium text-text-primary">Connection credentials</p>
      <p className="mt-1 text-xs text-text-secondary">
        Stored in Vault, never returned to the browser again.
      </p>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="text-xs font-semibold text-text-secondary">Host</span>
          <input
            type="text"
            value={host}
            onChange={(event) => setHost(event.target.value)}
            className="mt-1 w-full rounded-sm border border-border bg-bg px-3 py-2 text-sm text-text-primary"
          />
        </label>
        <label className="block text-sm">
          <span className="text-xs font-semibold text-text-secondary">Port</span>
          <input
            type="number"
            value={port}
            onChange={(event) => setPort(Number(event.target.value))}
            className="mt-1 w-full rounded-sm border border-border bg-bg px-3 py-2 text-sm text-text-primary"
          />
        </label>
        <label className="block text-sm">
          <span className="text-xs font-semibold text-text-secondary">Username</span>
          <input
            type="text"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            className="mt-1 w-full rounded-sm border border-border bg-bg px-3 py-2 text-sm text-text-primary"
          />
        </label>
        <label className="block text-sm">
          <span className="text-xs font-semibold text-text-secondary">Password</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="new-password"
            className="mt-1 w-full rounded-sm border border-border bg-bg px-3 py-2 text-sm text-text-primary"
          />
        </label>
        <label className="block text-sm">
          <span className="text-xs font-semibold text-text-secondary">SSL mode</span>
          <select
            value={sslmode}
            onChange={(event) => setSslmode(event.target.value)}
            className="mt-1 w-full rounded-sm border border-border bg-bg px-3 py-2 text-sm text-text-primary"
          >
            <option value="disable">disable</option>
            <option value="require">require</option>
            <option value="verify-full">verify-full</option>
          </select>
        </label>
      </div>
      <div className="mt-4 flex gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={isPending}
          className="rounded-sm bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
        >
          Save credentials
        </button>
        <button
          type="button"
          onClick={onDone}
          className="rounded-sm border border-border px-4 py-1.5 text-sm text-text-primary hover:bg-bg"
        >
          Cancel
        </button>
      </div>
      {error && <p className="mt-3 text-sm text-danger">{error}</p>}
    </div>
  );
}
