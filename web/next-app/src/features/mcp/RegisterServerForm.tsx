"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { registerServer } from "./actions";
import type { ToolClass } from "./types";

const TOOL_CLASSES: ToolClass[] = ["read_metadata", "read_data", "external_read", "write", "admin"];

type DraftTool = { name: string; toolClass: ToolClass };

/** Stitch "MCP Server Governance & Tool Integrations" (Section 5.2): register
 * a server with its declared tool manifest -- undeclared tools are never
 * reachable (tool_policy.py), so the manifest is required up front, not
 * discovered later. */
export function RegisterServerForm({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [endpointUrl, setEndpointUrl] = useState("");
  const [authToken, setAuthToken] = useState("");
  const [tools, setTools] = useState<DraftTool[]>([{ name: "", toolClass: "read_metadata" }]);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function updateTool(index: number, patch: Partial<DraftTool>) {
    setTools((current) => current.map((t, i) => (i === index ? { ...t, ...patch } : t)));
  }

  function submit() {
    const declared = tools.filter((t) => t.name.trim());
    if (!name.trim() || !endpointUrl.trim() || declared.length === 0) {
      setError("Name, endpoint URL, and at least one declared tool are required.");
      return;
    }
    setError(null);
    startTransition(async () => {
      const result = await registerServer({
        name: name.trim(),
        endpointUrl: endpointUrl.trim(),
        authToken: authToken.trim() || null,
        tools: declared,
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
      <p className="text-sm font-medium text-text-primary">Register MCP server</p>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="text-xs font-semibold text-text-secondary">Name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 w-full rounded-sm border border-border px-3 py-2 text-sm text-text-primary"
          />
        </label>
        <label className="block text-sm">
          <span className="text-xs font-semibold text-text-secondary">Endpoint URL</span>
          <input
            type="text"
            value={endpointUrl}
            onChange={(e) => setEndpointUrl(e.target.value)}
            placeholder="https://mcp.example.com"
            className="mt-1 w-full rounded-sm border border-border px-3 py-2 text-sm text-text-primary"
          />
        </label>
        <label className="block text-sm sm:col-span-2">
          <span className="text-xs font-semibold text-text-secondary">Auth token (optional)</span>
          <input
            type="password"
            value={authToken}
            onChange={(e) => setAuthToken(e.target.value)}
            autoComplete="new-password"
            className="mt-1 w-full rounded-sm border border-border px-3 py-2 text-sm text-text-primary"
          />
        </label>
      </div>

      <div className="mt-4">
        <p className="text-xs font-semibold tracking-wide text-text-secondary uppercase">
          Declared tool manifest
        </p>
        {tools.map((tool, index) => (
          <div key={index} className="mt-2 flex gap-2">
            <input
              type="text"
              value={tool.name}
              onChange={(e) => updateTool(index, { name: e.target.value })}
              placeholder="tool_name"
              className="flex-1 rounded-sm border border-border px-2 py-1.5 text-sm text-text-primary"
            />
            <select
              value={tool.toolClass}
              onChange={(e) => updateTool(index, { toolClass: e.target.value as ToolClass })}
              className="rounded-sm border border-border px-2 py-1.5 text-sm text-text-primary"
            >
              {TOOL_CLASSES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
        ))}
        <button
          type="button"
          onClick={() => setTools((t) => [...t, { name: "", toolClass: "read_metadata" }])}
          className="mt-2 text-xs text-primary hover:underline"
        >
          + Add tool
        </button>
      </div>

      <div className="mt-4 flex gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={isPending}
          className="rounded-sm bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
        >
          Register
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
