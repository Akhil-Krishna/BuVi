"use client";

import { useState, useTransition } from "react";
import { createDashboard, pinArtifact } from "@/features/dashboards/actions";
import type { Dashboard } from "@/features/dashboards/types";

/** "Pin to Dashboard" (Section 32 Step D, Stitch "Executive Revenue & Margin
 * Synthesis -- Dashboard View"). Picking an existing dashboard or naming a
 * new one are the same action from the user's point of view, so this stays
 * one small inline panel rather than a separate "create dashboard" flow the
 * chat screen would have to link out to. */
export function PinToDashboardDialog({
  artifactId,
  dashboards,
  onClose,
  onPinned,
}: {
  artifactId: string;
  dashboards: Dashboard[];
  onClose: () => void;
  onPinned: (dashboardId: string) => void;
}) {
  const [selected, setSelected] = useState<string>(dashboards[0]?.id ?? "");
  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function submit() {
    setError(null);
    startTransition(async () => {
      let dashboardId = selected;
      if (!dashboardId) {
        if (newName.trim().length === 0) {
          setError("Choose a dashboard or name a new one.");
          return;
        }
        const created = await createDashboard(newName.trim());
        if (!created.ok) {
          setError(created.message);
          return;
        }
        dashboardId = created.data.id;
      }
      const pinned = await pinArtifact(dashboardId, artifactId);
      if (!pinned.ok) {
        setError(pinned.message);
        return;
      }
      onPinned(dashboardId);
    });
  }

  return (
    <div className="mt-3 border border-border bg-bg p-4">
      <p className="text-sm font-medium text-text-primary">Pin to dashboard</p>

      {dashboards.length > 0 && (
        <div className="mt-3 space-y-1">
          {dashboards.map((dashboard) => (
            <label key={dashboard.id} className="flex items-center gap-2 text-sm text-text-primary">
              <input
                type="radio"
                name="dashboard"
                checked={selected === dashboard.id}
                onChange={() => setSelected(dashboard.id)}
              />
              {dashboard.name}
            </label>
          ))}
          <label className="flex items-center gap-2 text-sm text-text-primary">
            <input
              type="radio"
              name="dashboard"
              checked={selected === ""}
              onChange={() => setSelected("")}
            />
            New dashboard
          </label>
        </div>
      )}

      {(selected === "" || dashboards.length === 0) && (
        <input
          type="text"
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
          placeholder="Dashboard name"
          className="mt-2 w-full max-w-xs rounded-sm border border-border px-3 py-2 text-sm text-text-primary"
        />
      )}

      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={isPending}
          className="rounded-sm bg-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
        >
          Pin
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
