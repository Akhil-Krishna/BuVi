"use server";

import { revalidatePath } from "next/cache";
import { callGateway, GatewayError } from "@/lib/api/gateway";
import type {
  ConnectivityTestResult,
  DataSource,
  DataSourceEngine,
  SqlGrant,
  SyncResult,
  TableDetail,
  TableSummary,
  TenantUser,
} from "./types";

export type ActionOutcome<T = void> =
  | ({ ok: true } & (T extends void ? Record<never, never> : { data: T }))
  | { ok: false; code: string; message: string; details: Record<string, unknown> };

function failure(error: unknown): {
  ok: false;
  code: string;
  message: string;
  details: Record<string, unknown>;
} {
  if (error instanceof GatewayError) {
    return { ok: false, code: error.code, message: error.message, details: error.details };
  }
  return { ok: false, code: "UNKNOWN", message: "Something went wrong. Try again.", details: {} };
}

export async function listDataSources(): Promise<DataSource[]> {
  const response = await callGateway<{ items: DataSource[] }>("/api/v1/data-sources");
  return response.items;
}

export async function createDataSource(input: {
  name: string;
  engine: DataSourceEngine;
  hostLabel: string;
  databaseName: string;
  allowedSchemas: string[];
}): Promise<ActionOutcome<DataSource>> {
  try {
    const data = await callGateway<DataSource>("/api/v1/data-sources", {
      method: "POST",
      body: {
        name: input.name,
        engine: input.engine,
        host_label: input.hostLabel,
        database_name: input.databaseName,
        allowed_schemas: input.allowedSchemas,
      },
    });
    revalidatePath("/data");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

/** Step-up protected (Section 7.3): a stolen session cookie alone must not be
 * able to point a data source at an attacker-controlled host. */
export async function setDataSourceSecret(
  dataSourceId: string,
  input: { host: string; port: number; username: string; password: string; sslmode: string }
): Promise<ActionOutcome<DataSource>> {
  try {
    const data = await callGateway<DataSource>(`/api/v1/data-sources/${dataSourceId}/secret`, {
      method: "POST",
      body: input,
    });
    revalidatePath("/data");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

export async function testDataSource(
  dataSourceId: string
): Promise<ActionOutcome<ConnectivityTestResult>> {
  try {
    const data = await callGateway<ConnectivityTestResult>(
      `/api/v1/data-sources/${dataSourceId}/test`,
      { method: "POST" }
    );
    revalidatePath("/data");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

export async function syncDataSource(dataSourceId: string): Promise<ActionOutcome<SyncResult>> {
  try {
    const data = await callGateway<SyncResult>(`/api/v1/data-sources/${dataSourceId}/sync`, {
      method: "POST",
    });
    revalidatePath("/data");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

export async function listTables(dataSourceId: string): Promise<TableSummary[]> {
  const response = await callGateway<{ items: TableSummary[]; next_cursor: string | null }>(
    `/api/v1/data-sources/${dataSourceId}/tables`
  );
  return response.items;
}

export async function getTable(
  dataSourceId: string,
  tableId: string
): Promise<ActionOutcome<TableDetail>> {
  try {
    const data = await callGateway<TableDetail>(
      `/api/v1/data-sources/${dataSourceId}/tables/${tableId}`
    );
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

export async function listSqlGrants(dataSourceId: string): Promise<SqlGrant[]> {
  const response = await callGateway<{ items: SqlGrant[] }>(
    `/api/v1/data-sources/${dataSourceId}/sql-grants`
  );
  return response.items;
}

/** `org_admin` only, server-enforced by role, not just a permission string. */
export async function grantSqlExecute(
  dataSourceId: string,
  userId: string
): Promise<ActionOutcome<SqlGrant>> {
  try {
    const data = await callGateway<SqlGrant>(`/api/v1/data-sources/${dataSourceId}/sql-grants`, {
      method: "POST",
      body: { user_id: userId },
    });
    revalidatePath("/data");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

export async function revokeSqlExecute(
  dataSourceId: string,
  grantId: string
): Promise<ActionOutcome> {
  try {
    await callGateway(`/api/v1/data-sources/${dataSourceId}/sql-grants/${grantId}`, {
      method: "DELETE",
    });
    revalidatePath("/data");
    return { ok: true };
  } catch (error) {
    return failure(error);
  }
}

/** Just enough of the tenant's user directory to pick a grantee (Section
 * 9's `GET /admin/users`, `org_admin`-only like the grant itself). Not a
 * shared admin module -- B7 owns the real admin user list; this is a narrow
 * read for this one picker. */
export async function listTenantUsersForGrant(): Promise<TenantUser[]> {
  const response = await callGateway<{ items: TenantUser[] }>("/api/v1/admin/users");
  return response.items;
}
