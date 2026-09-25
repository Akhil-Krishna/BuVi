"use client";

import { useState } from "react";
import { InviteForm } from "./InviteForm";
import { UserRow } from "./UserRow";
import type { AdminUser, Invitation } from "./types";

/** Stitch "User Management" (Section 5.2) -- also covers invitations. */
export function UsersWorkspace({
  users,
  invitations,
  selfUserId,
  hasWebauthn,
  hasAnyFactor,
}: {
  users: AdminUser[];
  invitations: Invitation[];
  selfUserId: string;
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
}) {
  const [showInvite, setShowInvite] = useState(false);

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-text-primary">User Management</h1>
        {!showInvite && (
          <button
            type="button"
            onClick={() => setShowInvite(true)}
            className="rounded-sm bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-hover"
          >
            Invite user
          </button>
        )}
      </div>

      {showInvite && (
        <div className="mt-4">
          <InviteForm
            hasWebauthn={hasWebauthn}
            hasAnyFactor={hasAnyFactor}
            onClose={() => setShowInvite(false)}
          />
        </div>
      )}

      {invitations.length > 0 && (
        <div className="mt-6">
          <p className="text-xs font-medium text-text-secondary">Pending invitations</p>
          <div className="mt-1 border border-border bg-bg">
            {invitations.map((inv) => (
              <div key={inv.id} className="flex items-center justify-between border-b border-border p-2 text-sm last:border-b-0">
                <span className="text-text-primary">{inv.email}</span>
                <span className="text-text-secondary">{inv.role_key}</span>
                <span className="text-text-secondary">{inv.status}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="mt-6 border border-border bg-bg">
        {users.map((user) => (
          <UserRow
            key={user.id}
            user={user}
            isSelf={user.id === selfUserId}
            hasWebauthn={hasWebauthn}
            hasAnyFactor={hasAnyFactor}
          />
        ))}
      </div>
    </div>
  );
}
