"use client";

import { useState } from "react";
import { CreateDataSourceForm } from "./CreateDataSourceForm";
import { DataSourceRow } from "./DataSourceRow";
import type { DataSource, TenantUser } from "./types";

/** Stitch "Data Sources" (Section 5.2). One page, no nested routes (Section
 * 4.2 lists only `data/page.tsx`) -- a connection's detail, secret entry,
 * test/sync, table browser, and sql-grants all expand inline per row rather
 * than navigating to `data/[id]`. */
export function DataSourcesWorkspace({
  dataSources,
  canManage,
  canReadCatalog,
  isOrgAdmin,
  hasWebauthn,
  hasAnyFactor,
  tenantUsers,
}: {
  dataSources: DataSource[];
  canManage: boolean;
  canReadCatalog: boolean;
  isOrgAdmin: boolean;
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
  tenantUsers: TenantUser[];
}) {
  const [showCreate, setShowCreate] = useState(false);

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-text-primary">Data Sources</h1>
        {canManage && !showCreate && (
          <button
            type="button"
            onClick={() => setShowCreate(true)}
            className="rounded-sm bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-hover"
          >
            Add Data Source
          </button>
        )}
      </div>

      {showCreate && (
        <div className="mt-4">
          <CreateDataSourceForm onClose={() => setShowCreate(false)} />
        </div>
      )}

      {dataSources.length === 0 ? (
        <p className="mt-6 text-sm text-text-secondary">
          No data sources connected yet{canManage ? " -- add one above." : "."}
        </p>
      ) : (
        <div className="mt-6 border border-border bg-bg">
          {dataSources.map((dataSource) => (
            <DataSourceRow
              key={dataSource.id}
              dataSource={dataSource}
              canManage={canManage}
              canReadCatalog={canReadCatalog}
              isOrgAdmin={isOrgAdmin}
              hasWebauthn={hasWebauthn}
              hasAnyFactor={hasAnyFactor}
              tenantUsers={tenantUsers}
            />
          ))}
        </div>
      )}
    </div>
  );
}
