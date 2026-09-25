"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { MfaVerifyForm } from "@/features/auth/MfaVerifyForm";
import {
  changeRoles,
  deactivateUser,
  resetUserMfa,
  revokeUserSessions,
} from "./actions";
import { TENANT_ROLES, type AdminUser } from "./types";

const STATUS_CLASS: Record<string, string> = {
  active: "border-success-border bg-success-bg text-success",
  invited: "border-warning-border bg-warning-bg text-warning",
  deactivated: "border-border bg-bg-subtle text-text-secondary",
};

type PendingAction = "roles" | "sessions" | "mfa" | "deactivate" | null;

export function UserRow({
  user,
  isSelf,
  hasWebauthn,
  hasAnyFactor,
}: {
  user: AdminUser;
  isSelf: boolean;
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
}) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const [roleSet, setRoleSet] = useState(new Set(user.roles));
  const [pending, setPending] = useState<PendingAction>(null);
  const [requireWebauthn, setRequireWebauthn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  // Reset the role checkboxes if the server prop changes underneath us
  // (a save from this same row, via router.refresh()) rather than in an effect.
  const [rolesLoadedFor, setRolesLoadedFor] = useState(user.roles);
  if (rolesLoadedFor !== user.roles) {
    setRolesLoadedFor(user.roles);
    setRoleSet(new Set(user.roles));
  }

  function handleStepUp(code: string, details: Record<string, unknown>, action: PendingAction) {
    if (code !== "STEP_UP_REQUIRED") return false;
    if (!hasAnyFactor) {
      setError("Add a two-factor method in Account settings, then try again.");
      return true;
    }
    setRequireWebauthn(details.method === "webauthn");
    setPending(action);
    return true;
  }

  function saveRoles() {
    setError(null);
    setInfo(null);
    const grant = TENANT_ROLES.filter((r) => roleSet.has(r) && !user.roles.includes(r));
    const revoke = user.roles.filter((r) => !roleSet.has(r));
    if (grant.length === 0 && revoke.length === 0) return;
    startTransition(async () => {
      const result = await changeRoles(user.id, grant, revoke);
      if (result.ok) {
        setPending(null);
        router.refresh();
        return;
      }
      if (handleStepUp(result.code, result.details, "roles")) return;
      setError(result.message);
    });
  }

  function revokeSessions() {
    setError(null);
    setInfo(null);
    startTransition(async () => {
      const result = await revokeUserSessions(user.id);
      if (result.ok) {
        setPending(null);
        setInfo(`${result.data.sessions_revoked} session(s) revoked.`);
        return;
      }
      if (handleStepUp(result.code, result.details, "sessions")) return;
      setError(result.message);
    });
  }

  function resetMfa() {
    setError(null);
    setInfo(null);
    startTransition(async () => {
      const result = await resetUserMfa(user.id);
      if (result.ok) {
        setPending(null);
        router.refresh();
        setInfo(
          `${result.data.factors_revoked} factor(s) and ${result.data.sessions_revoked} session(s) revoked.`
        );
        return;
      }
      if (handleStepUp(result.code, result.details, "mfa")) return;
      setError(result.message);
    });
  }

  function deactivate() {
    setError(null);
    setInfo(null);
    startTransition(async () => {
      const result = await deactivateUser(user.id);
      if (result.ok) {
        setPending(null);
        router.refresh();
        return;
      }
      if (handleStepUp(result.code, result.details, "deactivate")) return;
      setError(result.message);
    });
  }

  const resume: Record<Exclude<PendingAction, null>, () => void> = {
    roles: saveRoles,
    sessions: revokeSessions,
    mfa: resetMfa,
    deactivate: deactivate,
  };

  return (
    <div className="border-b border-border last:border-b-0" data-testid={`user-row-${user.email}`}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-3 text-left hover:bg-row-hover"
      >
        <div>
          <p className="text-sm font-medium text-text-primary">
            {user.display_name} <span className="text-text-secondary">({user.email})</span>
          </p>
          <p className="mt-0.5 text-xs text-text-secondary">
            {user.roles.join(", ")} · MFA {user.mfa_enabled ? "on" : "off"}
          </p>
        </div>
        <span
          className={`rounded-sm border px-2 py-0.5 text-xs font-medium ${STATUS_CLASS[user.status] ?? STATUS_CLASS.deactivated}`}
        >
          {user.status}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-border bg-bg-subtle p-3">
          {pending ? (
            <div className="max-w-sm">
              <MfaVerifyForm
                hasWebauthn={hasWebauthn}
                requireWebauthn={requireWebauthn}
                onVerified={resume[pending]}
              />
            </div>
          ) : (
            <>
              <p className="text-xs font-medium text-text-secondary">Roles</p>
              <div className="mt-1 flex flex-wrap gap-3">
                {TENANT_ROLES.map((role) => (
                  <label key={role} className="flex items-center gap-1.5 text-sm text-text-primary">
                    <input
                      type="checkbox"
                      checked={roleSet.has(role)}
                      onChange={(e) => {
                        const next = new Set(roleSet);
                        if (e.target.checked) next.add(role);
                        else next.delete(role);
                        setRoleSet(next);
                      }}
                    />
                    {role}
                  </label>
                ))}
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={saveRoles}
                  disabled={isPending}
                  className="rounded-sm border border-border px-3 py-1.5 text-sm text-text-primary hover:bg-bg disabled:opacity-50"
                >
                  Save roles
                </button>
                <button
                  type="button"
                  onClick={revokeSessions}
                  disabled={isPending}
                  className="rounded-sm border border-border px-3 py-1.5 text-sm text-text-primary hover:bg-bg disabled:opacity-50"
                >
                  Revoke sessions
                </button>
                {!isSelf && (
                  <button
                    type="button"
                    onClick={resetMfa}
                    disabled={isPending}
                    className="rounded-sm border border-border px-3 py-1.5 text-sm text-text-primary hover:bg-bg disabled:opacity-50"
                  >
                    Reset MFA
                  </button>
                )}
                {user.status !== "deactivated" && (
                  <button
                    type="button"
                    onClick={deactivate}
                    disabled={isPending}
                    className="rounded-sm border border-border px-3 py-1.5 text-sm text-danger hover:bg-danger hover:text-white disabled:opacity-50"
                  >
                    Deactivate
                  </button>
                )}
              </div>
            </>
          )}
          {info && <p className="mt-2 text-sm text-success">{info}</p>}
          {error && <p className="mt-2 text-sm text-danger">{error}</p>}
        </div>
      )}
    </div>
  );
}
