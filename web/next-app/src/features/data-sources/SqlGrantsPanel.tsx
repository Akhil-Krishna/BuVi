"use client";

import { useState, useTransition } from "react";
import { grantSqlExecute, revokeSqlExecute } from "./actions";
import type { SqlGrant, TenantUser } from "./types";
import { formatDateTime } from "@/lib/format";

/** `org_admin`-only (Section 7.1): who else, beyond a blanket `sql:execute`
 * grant, may run SQL against this one connection. Server-enforced by role,
 * not just a permission string -- this panel only renders for org_admin, but
 * the endpoint refuses everyone else regardless.
 *
 * `grants` is fetched by the parent row on expand, not a server-component
 * prop -- `router.refresh()` re-renders the page but never touches that
 * client-fetched state, so a successful grant/revoke calls `onChanged`
 * instead, which re-fetches it directly. */
export function SqlGrantsPanel({
  dataSourceId,
  grants,
  users,
  onChanged,
}: {
  dataSourceId: string;
  grants: SqlGrant[];
  users: TenantUser[];
  onChanged: () => void;
}) {
  const [selected, setSelected] = useState(users[0]?.id ?? "");
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const grantedIds = new Set(grants.map((grant) => grant.user_id));
  const usersById = new Map(users.map((user) => [user.id, user]));
  const grantable = users.filter((user) => !grantedIds.has(user.id));

  function grant() {
    if (!selected) return;
    setError(null);
    startTransition(async () => {
      const result = await grantSqlExecute(dataSourceId, selected);
      if (!result.ok) setError(result.message);
      else onChanged();
    });
  }

  function revoke(grantId: string) {
    setError(null);
    startTransition(async () => {
      const result = await revokeSqlExecute(dataSourceId, grantId);
      if (!result.ok) setError(result.message);
      else onChanged();
    });
  }

  return (
    <div>
      {grants.length > 0 && (
        <div className="mb-3 overflow-x-auto border border-border">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-border bg-bg-subtle">
                <th className="px-3 py-2 font-semibold text-text-secondary">User</th>
                <th className="px-3 py-2 font-semibold text-text-secondary">Granted</th>
                <th className="px-3 py-2 text-right font-semibold text-text-secondary">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {grants.map((grantRow) => (
                <tr
                  key={grantRow.id}
                  className="border-b border-border last:border-b-0 hover:bg-row-hover"
                >
                  <td className="px-3 py-2 text-text-primary">
                    {usersById.get(grantRow.user_id)?.email ?? grantRow.user_id}
                  </td>
                  <td className="px-3 py-2 text-text-secondary">
                    {formatDateTime(grantRow.granted_at)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => revoke(grantRow.id)}
                      disabled={isPending}
                      className="rounded-sm border border-border px-2 py-1 text-xs text-danger hover:bg-danger hover:text-white disabled:opacity-50"
                    >
                      Revoke grant
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {grantable.length > 0 ? (
        <div className="flex items-center gap-2">
          <select
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
            className="rounded-sm border border-border px-3 py-1.5 text-sm text-text-primary"
          >
            {grantable.map((user) => (
              <option key={user.id} value={user.id}>
                {user.email} ({user.roles.join(", ") || "no role"})
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={grant}
            disabled={isPending}
            className="rounded-sm bg-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
          >
            Grant sql:execute
          </button>
        </div>
      ) : (
        <p className="text-sm text-text-secondary">Every tenant user already holds a grant.</p>
      )}
      {error && <p className="mt-2 text-sm text-danger">{error}</p>}
    </div>
  );
}
