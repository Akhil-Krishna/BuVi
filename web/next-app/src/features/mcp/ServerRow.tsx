"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { MfaVerifyForm } from "@/features/auth/MfaVerifyForm";
import type { TenantUser } from "@/features/data-sources/types";
import {
  approveServer,
  disableServer,
  getServer,
  grantTool,
  invokeTool,
  rejectServer,
  revokeToolGrant,
} from "./actions";
import type { InvokeResult, Server, ServerDetail, Tool } from "./types";

const STATUS_CLASS: Record<Server["status"], string> = {
  pending_approval: "border-warning-border bg-warning-bg text-warning",
  approved: "border-success-border bg-success-bg text-success",
  disabled: "border-border bg-bg-subtle text-text-secondary",
  rejected: "border-danger-border bg-danger-bg text-danger",
};

function ToolRow({
  serverId,
  tool,
  isOrgAdmin,
  users,
  hasWebauthn,
  hasAnyFactor,
  onChanged,
}: {
  serverId: string;
  tool: Tool;
  isOrgAdmin: boolean;
  users: TenantUser[];
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
  onChanged: () => void;
}) {
  const [grantValue, setGrantValue] = useState(""); // "role:xxx" or "user:uuid"
  const [args, setArgs] = useState("{}");
  const [result, setResult] = useState<InvokeResult | null>(null);
  const [pendingStepUp, setPendingStepUp] = useState(false);
  const [requireWebauthn, setRequireWebauthn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function grant() {
    if (!grantValue) return;
    setError(null);
    const [kind, value] = grantValue.split(":", 2);
    startTransition(async () => {
      const result =
        kind === "role"
          ? await grantTool(serverId, tool.name, { role: value })
          : await grantTool(serverId, tool.name, { userId: value });
      if (!result.ok) setError(result.message);
      else onChanged();
    });
  }

  function revoke(grantId: string) {
    setError(null);
    startTransition(async () => {
      const result = await revokeToolGrant(serverId, tool.name, grantId);
      if (!result.ok) setError(result.message);
      else onChanged();
    });
  }

  function invoke() {
    setError(null);
    setResult(null);
    setPendingStepUp(false);
    let parsed: Record<string, unknown>;
    try {
      parsed = args.trim() ? JSON.parse(args) : {};
    } catch {
      setError("Arguments must be valid JSON.");
      return;
    }
    startTransition(async () => {
      const outcome = await invokeTool(serverId, tool.name, parsed);
      if (outcome.ok) {
        setResult(outcome.data);
        return;
      }
      if (outcome.code === "STEP_UP_REQUIRED") {
        if (!hasAnyFactor) {
          setError("Add a two-factor method in Account settings, then try again.");
          return;
        }
        setRequireWebauthn(outcome.details.method === "webauthn");
        setPendingStepUp(true);
        return;
      }
      setError(outcome.message);
    });
  }

  return (
    <div className="border-t border-border p-3" data-testid={`tool-row-${tool.name}`}>
      <div className="flex items-center justify-between">
        <p className="font-mono text-sm text-text-primary">
          {tool.name} <span className="text-xs text-text-secondary">({tool.tool_class})</span>
        </p>
      </div>

      {tool.grants.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {tool.grants.map((g) => (
            <li
              key={g.id}
              className="flex items-center justify-between text-xs text-text-secondary"
            >
              <span>
                {g.grantee_role ? `role: ${g.grantee_role}` : `user: ${g.grantee_user_id}`}
              </span>
              {isOrgAdmin && (
                <button
                  type="button"
                  onClick={() => revoke(g.id)}
                  className="text-danger hover:underline"
                >
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {isOrgAdmin && (
        <div className="mt-2 flex items-center gap-2">
          <select
            value={grantValue}
            onChange={(e) => setGrantValue(e.target.value)}
            className="rounded-sm border border-border px-2 py-1 text-xs text-text-primary"
          >
            <option value="">Grant to...</option>
            <option value="role:client">role: client</option>
            <option value="role:developer">role: developer</option>
            <option value="role:org_admin">role: org_admin</option>
            {users.map((u) => (
              <option key={u.id} value={`user:${u.id}`}>
                user: {u.email}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={grant}
            disabled={isPending || !grantValue}
            className="rounded-sm border border-border px-2 py-1 text-xs text-text-primary hover:bg-bg-subtle disabled:opacity-50"
          >
            Grant
          </button>
        </div>
      )}

      {pendingStepUp ? (
        <div className="mt-2">
          <MfaVerifyForm
            hasWebauthn={hasWebauthn}
            requireWebauthn={requireWebauthn}
            onVerified={() => {
              setPendingStepUp(false);
              invoke();
            }}
          />
        </div>
      ) : (
        <div className="mt-2">
          <textarea
            value={args}
            onChange={(e) => setArgs(e.target.value)}
            rows={2}
            className="w-full rounded-sm border border-border px-2 py-1 font-mono text-xs text-text-primary"
          />
          <button
            type="button"
            onClick={invoke}
            disabled={isPending}
            className="mt-1 rounded-sm border border-border px-2 py-1 text-xs text-text-primary hover:bg-bg-subtle disabled:opacity-50"
          >
            Invoke
          </button>
        </div>
      )}

      {result && (
        <p className={`mt-2 text-xs ${result.is_error ? "text-danger" : "text-success"}`}>
          {result.content.map((c) => c.text).join(" ") || (result.is_error ? "Error" : "OK")}
        </p>
      )}
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
    </div>
  );
}

export function ServerRow({
  server,
  isOrgAdmin,
  users,
  hasWebauthn,
  hasAnyFactor,
}: {
  server: Server;
  isOrgAdmin: boolean;
  users: TenantUser[];
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
}) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<ServerDetail | null>(null);
  const [pendingStepUp, setPendingStepUp] = useState(false);
  const [requireWebauthn, setRequireWebauthn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function refreshDetail() {
    getServer(server.id).then((result) => {
      if (result.ok) setDetail(result.data);
    });
  }

  function toggle() {
    const next = !expanded;
    setExpanded(next);
    if (next && !detail) refreshDetail();
  }

  function approve() {
    setError(null);
    setPendingStepUp(false);
    startTransition(async () => {
      const result = await approveServer(server.id);
      if (result.ok) {
        router.refresh();
        refreshDetail();
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

  function reject() {
    setError(null);
    startTransition(async () => {
      const result = await rejectServer(server.id);
      if (result.ok) {
        router.refresh();
        refreshDetail();
      } else setError(result.message);
    });
  }

  function disable() {
    setError(null);
    startTransition(async () => {
      const result = await disableServer(server.id);
      if (result.ok) {
        router.refresh();
        refreshDetail();
      } else setError(result.message);
    });
  }

  return (
    <div className="border-b border-border last:border-b-0" data-testid={`server-row-${server.name}`}>
      <button
        type="button"
        onClick={toggle}
        className="flex w-full items-center justify-between px-3 py-3 text-left hover:bg-row-hover"
      >
        <div>
          <p className="text-sm font-medium text-text-primary">{server.name}</p>
          <p className="text-xs text-text-secondary">{server.endpoint_url}</p>
        </div>
        <span
          className={`rounded-sm border px-2 py-0.5 text-xs font-medium ${STATUS_CLASS[server.status]}`}
        >
          {server.status}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-border bg-bg-subtle p-3">
          {isOrgAdmin && (
            <div className="flex flex-wrap gap-2">
              {server.status === "pending_approval" &&
                (pendingStepUp ? (
                  <div className="w-full max-w-sm">
                    <MfaVerifyForm
                      hasWebauthn={hasWebauthn}
                      requireWebauthn={requireWebauthn}
                      onVerified={() => {
                        setPendingStepUp(false);
                        approve();
                      }}
                    />
                  </div>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={approve}
                      disabled={isPending}
                      className="rounded-sm border border-border px-3 py-1.5 text-sm text-success hover:bg-success hover:text-white disabled:opacity-50"
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      onClick={reject}
                      disabled={isPending}
                      className="rounded-sm border border-border px-3 py-1.5 text-sm text-danger hover:bg-danger hover:text-white disabled:opacity-50"
                    >
                      Reject
                    </button>
                  </>
                ))}
              {server.status === "approved" && (
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

          {!detail ? (
            <p className="mt-3 text-sm text-text-secondary">Loading tools...</p>
          ) : (
            <div className="mt-3 border border-border bg-bg">
              {detail.tools.map((tool) => (
                <ToolRow
                  key={tool.id}
                  serverId={server.id}
                  tool={tool}
                  isOrgAdmin={isOrgAdmin}
                  users={users}
                  hasWebauthn={hasWebauthn}
                  hasAnyFactor={hasAnyFactor}
                  onChanged={refreshDetail}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
