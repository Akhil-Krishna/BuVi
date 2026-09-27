"use client";

import type { Conversation } from "@/features/analytics/types";

/** Chat history, Claude/ChatGPT-style: a user's own past conversations, newest first
 * (`GET /conversations`), clickable to resume. No dedicated Stitch screen -- follows the
 * app's existing list/row treatment (a plain bordered list, no cards). */
export function ConversationSidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <div className="flex w-64 shrink-0 flex-col border-r border-border-subtle">
      <div className="p-3">
        <button
          type="button"
          onClick={onNew}
          className="w-full rounded-md border border-border px-3 py-1.5 text-sm text-text-primary transition-colors hover:bg-bg-elevated"
        >
          + New chat
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {conversations.length === 0 ? (
          <p className="px-2 py-1 text-xs text-text-muted">No conversations yet.</p>
        ) : (
          conversations.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => onSelect(c.id)}
              aria-current={c.id === activeId}
              title={c.title ?? "Untitled"}
              className={`mb-0.5 block w-full truncate rounded-md px-2.5 py-1.5 text-left text-sm transition-colors ${
                c.id === activeId
                  ? "bg-primary-muted text-primary"
                  : "text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
              }`}
            >
              {c.title ?? "Untitled"}
            </button>
          ))
        )}
      </div>
    </div>
  );
}
