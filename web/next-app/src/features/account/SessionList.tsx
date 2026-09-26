"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { revokeSession, type UserSession } from "./session-actions";
import { formatDateTime } from "@/lib/format";

export function SessionList({ sessions }: { sessions: UserSession[] }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  function revoke(session: UserSession) {
    setError(null);
    setPendingId(session.id);
    startTransition(async () => {
      const result = await revokeSession(session.id);
      setPendingId(null);
      if (result.ok) {
        // Revoking the session you are sitting in ends it: the next request
        // has no valid cookie, so proxy.ts sends the browser to /login.
        router.refresh();
      } else {
        setError(result.message);
      }
    });
  }

  if (sessions.length === 0) {
    return <p className="text-sm text-text-secondary">No active sessions.</p>;
  }

  return (
    <div>
      <div className="overflow-x-auto border border-border">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-border bg-bg-subtle">
              <th className="px-3 py-2 text-xs font-semibold text-text-secondary">Device</th>
              <th className="px-3 py-2 text-xs font-semibold text-text-secondary">IP address</th>
              <th className="px-3 py-2 text-xs font-semibold text-text-secondary">Last seen</th>
              <th className="px-3 py-2 text-xs font-semibold text-text-secondary">Expires</th>
              <th className="px-3 py-2 text-xs font-semibold text-text-secondary">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((session, index) => (
              <tr
                key={session.id}
                className={`border-b border-border last:border-b-0 hover:bg-row-hover ${
                  index % 2 === 1 ? "bg-bg-subtle" : "bg-bg"
                }`}
              >
                <td className="px-3 py-2 text-sm text-text-primary">
                  {session.device_label ?? session.user_agent ?? "Unknown device"}
                  {session.current && (
                    <span className="ml-2 border border-border bg-bg-subtle px-1.5 py-0.5 text-xs text-text-secondary">
                      This session
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-text-secondary">
                  {session.ip_address ?? "—"}
                </td>
                <td className="px-3 py-2 text-sm text-text-secondary">
                  {formatDateTime(session.last_seen_at)}
                </td>
                <td className="px-3 py-2 text-sm text-text-secondary">
                  {formatDateTime(session.expires_at)}
                </td>
                <td className="px-3 py-2 text-right">
                  <button
                    type="button"
                    onClick={() => revoke(session)}
                    disabled={pendingId === session.id}
                    className="rounded-sm border border-border px-2 py-1 text-xs text-danger hover:bg-danger hover:text-white disabled:opacity-50"
                  >
                    {session.current ? "Revoke this session" : "Revoke session"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {error && <p className="mt-3 text-sm text-danger">{error}</p>}
    </div>
  );
}
