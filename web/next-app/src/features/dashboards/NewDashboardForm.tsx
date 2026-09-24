"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { createDashboard } from "./actions";

export function NewDashboardForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function submit() {
    if (name.trim().length === 0) return;
    setError(null);
    startTransition(async () => {
      const result = await createDashboard(name.trim());
      if (!result.ok) {
        setError(result.message);
        return;
      }
      setName("");
      router.push(`/dashboards/${result.data.id}`);
    });
  }

  return (
    <div className="flex items-start gap-2">
      <div>
        <input
          type="text"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Dashboard name"
          className="rounded-sm border border-border px-3 py-1.5 text-sm text-text-primary"
        />
        {error && <p className="mt-1 text-sm text-danger">{error}</p>}
      </div>
      <button
        type="button"
        onClick={submit}
        disabled={isPending || name.trim().length === 0}
        className="rounded-sm bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
      >
        New Dashboard
      </button>
    </div>
  );
}
