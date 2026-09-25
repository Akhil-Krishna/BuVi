"use client";

import { useState } from "react";
import type { TenantUser } from "@/features/data-sources/types";
import { RegisterServerForm } from "./RegisterServerForm";
import { ServerRow } from "./ServerRow";
import type { Server } from "./types";

/** Stitch "MCP Server Governance & Tool Integrations" (Section 5.2). */
export function McpWorkspace({
  servers,
  isOrgAdmin,
  users,
  hasWebauthn,
  hasAnyFactor,
}: {
  servers: Server[];
  isOrgAdmin: boolean;
  users: TenantUser[];
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
}) {
  const [showRegister, setShowRegister] = useState(false);

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-text-primary">MCP Server Governance</h1>
        {!showRegister && (
          <button
            type="button"
            onClick={() => setShowRegister(true)}
            className="rounded-sm bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-hover"
          >
            Register server
          </button>
        )}
      </div>

      {showRegister && (
        <div className="mt-4">
          <RegisterServerForm onClose={() => setShowRegister(false)} />
        </div>
      )}

      {servers.length === 0 ? (
        <p className="mt-6 text-sm text-text-secondary">No MCP servers registered yet.</p>
      ) : (
        <div className="mt-6 border border-border bg-bg">
          {servers.map((server) => (
            <ServerRow
              key={server.id}
              server={server}
              isOrgAdmin={isOrgAdmin}
              users={users}
              hasWebauthn={hasWebauthn}
              hasAnyFactor={hasAnyFactor}
            />
          ))}
        </div>
      )}
    </div>
  );
}
