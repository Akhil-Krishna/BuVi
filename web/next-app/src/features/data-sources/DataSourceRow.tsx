"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { listSqlGrants, syncDataSource, testDataSource } from "./actions";
import { SecretForm } from "./SecretForm";
import { SqlGrantsPanel } from "./SqlGrantsPanel";
import { TableBrowser } from "./TableBrowser";
import type { ConnectivityTestResult, DataSource, SqlGrant, SyncResult, TenantUser } from "./types";

const STATUS_CLASS: Record<DataSource["status"], string> = {
  pending: "border-warning-border bg-warning-bg text-warning",
  active: "border-success-border bg-success-bg text-success",
  error: "border-danger-border bg-danger-bg text-danger",
};

function formatWhen(value: string | null): string {
  if (!value) return "Never";
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function DataSourceRow({
  dataSource,
  canManage,
  canReadCatalog,
  isOrgAdmin,
  hasWebauthn,
  hasAnyFactor,
  tenantUsers,
}: {
  dataSource: DataSource;
  canManage: boolean;
  canReadCatalog: boolean;
  isOrgAdmin: boolean;
  hasWebauthn: boolean;
  hasAnyFactor: boolean;
  tenantUsers: TenantUser[];
}) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const [showSecretForm, setShowSecretForm] = useState(dataSource.status === "pending");
  const [testResult, setTestResult] = useState<ConnectivityTestResult | null>(null);
  const [syncResult, setSyncResult] = useState<SyncResult | null>(null);
  const [grants, setGrants] = useState<SqlGrant[] | null>(null);
  const [isPending, startTransition] = useTransition();

  function refreshGrants() {
    listSqlGrants(dataSource.id).then(setGrants);
  }

  function toggle() {
    const next = !expanded;
    setExpanded(next);
    if (next && isOrgAdmin && !grants) {
      refreshGrants();
    }
  }

  function runTest() {
    setTestResult(null);
    startTransition(async () => {
      const result = await testDataSource(dataSource.id);
      if (result.ok) {
        setTestResult(result.data);
        router.refresh();
      } else {
        setTestResult({
          data_source_id: dataSource.id,
          ok: false,
          code: result.code,
          message: result.message,
          tables_discovered: null,
          latency_ms: 0,
          status: dataSource.status,
        });
      }
    });
  }

  function runSync() {
    setSyncResult(null);
    startTransition(async () => {
      const result = await syncDataSource(dataSource.id);
      if (result.ok) {
        setSyncResult(result.data);
        router.refresh();
      }
    });
  }

  return (
    <div className="border-b border-border last:border-b-0">
      <button
        type="button"
        onClick={toggle}
        className="flex w-full items-center justify-between px-3 py-3 text-left hover:bg-row-hover"
      >
        <div>
          <p className="text-sm font-medium text-text-primary">{dataSource.name}</p>
          <p className="text-xs text-text-secondary">
            {dataSource.host_label} · {dataSource.engine}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-text-secondary">
            Last synced {formatWhen(dataSource.last_sync_at)}
          </span>
          <span
            className={`rounded-sm border px-2 py-0.5 text-xs font-medium ${STATUS_CLASS[dataSource.status]}`}
          >
            {dataSource.status}
          </span>
        </div>
      </button>

      {expanded && (
        <div className="border-t border-border bg-bg-subtle p-4">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-xs text-text-secondary">Database</dt>
              <dd className="text-text-primary">{dataSource.database_name}</dd>
            </div>
            <div>
              <dt className="text-xs text-text-secondary">Schemas</dt>
              <dd className="text-text-primary">{dataSource.allowed_schemas.join(", ")}</dd>
            </div>
          </dl>

          {canManage && (
            <div className="mt-4 space-y-3">
              {showSecretForm ? (
                <SecretForm
                  dataSourceId={dataSource.id}
                  engine={dataSource.engine}
                  hasWebauthn={hasWebauthn}
                  hasAnyFactor={hasAnyFactor}
                  onDone={() => setShowSecretForm(false)}
                />
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setShowSecretForm(true)}
                    className="rounded-sm border border-border px-3 py-1.5 text-sm text-text-primary hover:bg-bg"
                  >
                    {dataSource.status === "pending" ? "Set credentials" : "Update credentials"}
                  </button>
                  <button
                    type="button"
                    onClick={runTest}
                    disabled={isPending}
                    className="rounded-sm border border-border px-3 py-1.5 text-sm text-text-primary hover:bg-bg disabled:opacity-50"
                  >
                    Test connection
                  </button>
                  <button
                    type="button"
                    onClick={runSync}
                    disabled={isPending}
                    className="rounded-sm border border-border px-3 py-1.5 text-sm text-text-primary hover:bg-bg disabled:opacity-50"
                  >
                    Sync catalog
                  </button>
                </div>
              )}
              {testResult && (
                <p className={`text-sm ${testResult.ok ? "text-success" : "text-danger"}`}>
                  {testResult.message}
                </p>
              )}
              {syncResult && (
                <p className={`text-sm ${syncResult.ok ? "text-success" : "text-danger"}`}>
                  {syncResult.message}
                </p>
              )}
            </div>
          )}

          {canReadCatalog && (
            <div className="mt-4">
              <p className="mb-2 text-xs font-semibold tracking-wide text-text-secondary uppercase">
                Tables
              </p>
              {/* Remount on a new sync -- otherwise this only ever fetches the
                  table list once, from before the sync that populated it. */}
              <TableBrowser
                key={dataSource.last_sync_at ?? "never-synced"}
                dataSourceId={dataSource.id}
              />
            </div>
          )}

          {isOrgAdmin && (
            <div className="mt-4">
              <p className="mb-2 text-xs font-semibold tracking-wide text-text-secondary uppercase">
                sql:execute grants
              </p>
              {grants ? (
                <SqlGrantsPanel
                  dataSourceId={dataSource.id}
                  grants={grants}
                  users={tenantUsers}
                  onChanged={refreshGrants}
                />
              ) : (
                <p className="text-sm text-text-secondary">Loading...</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
