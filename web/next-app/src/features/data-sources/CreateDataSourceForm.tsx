"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { createDataSource } from "./actions";
import { SchemaTagInput } from "./SchemaTagInput";
import type { DataSourceEngine } from "./types";

const ENGINES: { value: DataSourceEngine; label: string }[] = [
  { value: "postgres", label: "PostgreSQL" },
  { value: "mysql", label: "MySQL" },
  { value: "snowflake", label: "Snowflake" },
  { value: "bigquery", label: "BigQuery" },
  { value: "redshift", label: "Redshift" },
];

/** Non-secret connection metadata only (Section 13.1) -- credentials are a
 * separate step (`SecretForm`) once this creates the `pending` row. */
export function CreateDataSourceForm({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [engine, setEngine] = useState<DataSourceEngine>("postgres");
  const [hostLabel, setHostLabel] = useState("");
  const [databaseName, setDatabaseName] = useState("");
  const [schemas, setSchemas] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function submit() {
    if (!name.trim() || !hostLabel.trim() || !databaseName.trim() || schemas.length === 0) {
      setError("Name, display label, database name, and at least one schema are required.");
      return;
    }
    setError(null);
    startTransition(async () => {
      const result = await createDataSource({
        name: name.trim(),
        engine,
        hostLabel: hostLabel.trim(),
        databaseName: databaseName.trim(),
        allowedSchemas: schemas,
      });
      if (!result.ok) {
        setError(result.message);
        return;
      }
      router.refresh();
      onClose();
    });
  }

  return (
    <div className="border border-border bg-bg p-4">
      <p className="text-sm font-medium text-text-primary">Add data source</p>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="text-xs font-semibold text-text-secondary">Name</span>
          <input
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="sample-sales-db"
            className="mt-1 w-full rounded-sm border border-border px-3 py-2 text-sm text-text-primary"
          />
        </label>
        <label className="block text-sm">
          <span className="text-xs font-semibold text-text-secondary">Engine</span>
          <select
            value={engine}
            onChange={(event) => setEngine(event.target.value as DataSourceEngine)}
            className="mt-1 w-full rounded-sm border border-border px-3 py-2 text-sm text-text-primary"
          >
            {ENGINES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm sm:col-span-2">
          <span className="text-xs font-semibold text-text-secondary">Display label</span>
          <input
            type="text"
            value={hostLabel}
            onChange={(event) => setHostLabel(event.target.value)}
            placeholder="Production PostgreSQL Replica"
            className="mt-1 w-full rounded-sm border border-border px-3 py-2 text-sm text-text-primary"
          />
        </label>
        <label className="block text-sm">
          <span className="text-xs font-semibold text-text-secondary">Database name</span>
          <input
            type="text"
            value={databaseName}
            onChange={(event) => setDatabaseName(event.target.value)}
            className="mt-1 w-full rounded-sm border border-border px-3 py-2 text-sm text-text-primary"
          />
        </label>
        <div className="sm:col-span-2">
          <span className="text-xs font-semibold text-text-secondary">Allowed schemas</span>
          <div className="mt-1">
            <SchemaTagInput schemas={schemas} onChange={setSchemas} />
          </div>
        </div>
      </div>

      <div className="mt-4 flex gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={isPending}
          className="rounded-sm bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
        >
          Save data source
        </button>
        <button
          type="button"
          onClick={onClose}
          className="rounded-sm border border-border px-4 py-1.5 text-sm text-text-primary hover:bg-bg-subtle"
        >
          Cancel
        </button>
      </div>
      {error && <p className="mt-3 text-sm text-danger">{error}</p>}
    </div>
  );
}
