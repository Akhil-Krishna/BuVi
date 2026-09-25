"use client";

import { useState, type KeyboardEvent } from "react";

/** Stitch "Data Sources": allowed schemas as removable tags. `additionalProperties: false` on
 * the wire means this is the whole shape the form needs to produce -- a plain string array. */
export function SchemaTagInput({
  schemas,
  onChange,
}: {
  schemas: string[];
  onChange: (schemas: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  function commit() {
    const value = draft.trim();
    if (value && !schemas.includes(value)) onChange([...schemas, value]);
    setDraft("");
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit();
    } else if (event.key === "Backspace" && draft === "" && schemas.length > 0) {
      onChange(schemas.slice(0, -1));
    }
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5 rounded-sm border border-border px-2 py-1.5">
        {schemas.map((schema) => (
          <span
            key={schema}
            className="flex items-center gap-1 rounded-sm bg-bg-subtle px-2 py-0.5 text-xs text-text-primary"
          >
            {schema}
            <button
              type="button"
              onClick={() => onChange(schemas.filter((s) => s !== schema))}
              aria-label={`Remove ${schema}`}
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
          placeholder={schemas.length === 0 ? "public, analytics, ..." : "Add schema"}
          className="min-w-[8rem] flex-1 border-0 py-0.5 text-sm text-text-primary outline-none"
        />
      </div>
      <p className="mt-1 text-xs text-text-secondary">Press Enter or comma to add a schema.</p>
    </div>
  );
}
