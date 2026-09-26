"use client";

import { useState, useTransition } from "react";
import { listAuditEvents } from "./actions";
import type { AuditEvent } from "./types";
import { formatDateTime } from "@/lib/format";

/** Stitch "Audit Log" (Section 5.2). Read-only: there is no write endpoint --
 * the log is append-only, populated as a side effect of the audited actions. */
export function AuditPanel({ events }: { events: AuditEvent[] }) {
  const [rows, setRows] = useState(events);
  const [filter, setFilter] = useState("");
  const [isPending, startTransition] = useTransition();

  function apply() {
    startTransition(async () => {
      setRows(await listAuditEvents(filter.trim() || undefined));
    });
  }

  return (
    <div>
      <h1 className="text-xl font-semibold text-text-primary">Audit Log</h1>
      <div className="mt-4 flex items-end gap-3">
        <div>
          <label className="block text-xs font-medium text-text-secondary" htmlFor="audit-filter">
            Event type
          </label>
          <input
            id="audit-filter"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="e.g. identity.role.changed"
            className="mt-1 w-64 rounded-sm border border-border px-2 py-1 text-sm text-text-primary"
          />
        </div>
        <button
          type="button"
          onClick={apply}
          disabled={isPending}
          className="rounded-sm border border-border px-3 py-1.5 text-sm text-text-primary hover:bg-bg-subtle disabled:opacity-50"
        >
          Filter
        </button>
      </div>

      <table className="mt-4 w-full border border-border bg-bg text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-text-secondary">
            <th className="px-3 py-2 font-medium">Time</th>
            <th className="px-3 py-2 font-medium">Event</th>
            <th className="px-3 py-2 font-medium">Actor</th>
            <th className="px-3 py-2 font-medium">Resource</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((event) => (
            <tr key={event.id} className="border-b border-border last:border-b-0">
              <td className="px-3 py-2 font-mono text-xs text-text-secondary">
                {formatDateTime(event.created_at)}
              </td>
              <td className="px-3 py-2 text-text-primary">{event.event_type}</td>
              <td className="px-3 py-2 text-text-secondary">{event.actor_type}</td>
              <td className="px-3 py-2 text-text-secondary">
                {event.resource_type ? `${event.resource_type}:${event.resource_id}` : "—"}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={4} className="px-3 py-4 text-center text-text-secondary">
                No audit events.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
