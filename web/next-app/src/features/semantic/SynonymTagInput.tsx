"use client";

import { useState, type KeyboardEvent } from "react";

/** Natural-language aliases the agent's lexical/semantic matcher checks
 * alongside the metric or dimension's own name (Section 12). Same tag-input
 * shape as data-sources' `SchemaTagInput` -- duplicated rather than shared,
 * since each is a five-line component scoped to its own feature and the
 * placeholder copy differs. */
export function SynonymTagInput({
  synonyms,
  onChange,
}: {
  synonyms: string[];
  onChange: (synonyms: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  function commit() {
    const value = draft.trim();
    if (value && !synonyms.includes(value)) onChange([...synonyms, value]);
    setDraft("");
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit();
    } else if (event.key === "Backspace" && draft === "" && synonyms.length > 0) {
      onChange(synonyms.slice(0, -1));
    }
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5 rounded-sm border border-border px-2 py-1.5">
        {synonyms.map((synonym) => (
          <span
            key={synonym}
            className="flex items-center gap-1 rounded-sm bg-bg-subtle px-2 py-0.5 text-xs text-text-primary"
          >
            {synonym}
            <button
              type="button"
              onClick={() => onChange(synonyms.filter((s) => s !== synonym))}
              aria-label={`Remove ${synonym}`}
              className="text-text-secondary hover:text-danger"
            >
              ×
            </button>
          </span>
        ))}
        <input
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={commit}
          placeholder={synonyms.length === 0 ? "revenue, income, top-line, ..." : "Add synonym"}
          className="min-w-[8rem] flex-1 border-0 py-0.5 text-sm text-text-primary outline-none"
        />
      </div>
      <p className="mt-1 text-xs text-text-secondary">
        Press Enter or comma to add. The agent maps conversational phrasing to these.
      </p>
    </div>
  );
}
