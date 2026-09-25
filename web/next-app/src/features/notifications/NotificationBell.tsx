"use client";

import { useState, useTransition } from "react";
import { markNotificationRead } from "./actions";
import type { Notification } from "./types";

/** No dedicated Stitch screen (Section 5.2 doesn't list one) -- a compact
 * dropdown anchored on the shared top nav, present for every authenticated
 * role alike (Phase A11's `GET /me/notifications` has no permission gate
 * beyond being signed in). */
export function NotificationBell({
  initialItems,
  initialUnread,
}: {
  initialItems: Notification[];
  initialUnread: number;
}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState(initialItems);
  const [unread, setUnread] = useState(initialUnread);
  const [, startTransition] = useTransition();

  function markRead(id: string) {
    setItems((current) => current.map((n) => (n.id === id ? { ...n, read_at: new Date().toISOString() } : n)));
    setUnread((n) => Math.max(0, n - 1));
    startTransition(async () => {
      await markNotificationRead(id);
    });
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="relative rounded-sm border border-border px-2 py-1 text-sm text-text-secondary hover:bg-bg-subtle"
      >
        Notifications
        {unread > 0 && (
          <span className="ml-1 rounded-sm bg-primary px-1.5 py-0.5 text-xs font-medium text-white">
            {unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-10 mt-1 w-80 border border-border bg-bg shadow-none">
          {items.length === 0 ? (
            <p className="p-3 text-sm text-text-secondary">No notifications.</p>
          ) : (
            items.map((n) => (
              <button
                key={n.id}
                type="button"
                onClick={() => markRead(n.id)}
                className={`block w-full border-b border-border p-3 text-left last:border-b-0 hover:bg-row-hover ${
                  n.read_at ? "" : "bg-bg-subtle"
                }`}
              >
                <p className="text-sm font-medium text-text-primary">{n.title}</p>
                <p className="mt-0.5 text-xs text-text-secondary">{n.body}</p>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
