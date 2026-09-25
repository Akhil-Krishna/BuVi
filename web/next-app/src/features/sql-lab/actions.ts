"use server";

import { callGateway, GatewayError } from "@/lib/api/gateway";
import type { SqlExecuteResult, SqlHistoryItem, SqlValidateResult } from "./types";

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

/** Dry validation -- the same validator/allow-list the chat flow uses
 * (Section 13), never a second one. No rows, no execution. */
export async function validateSql(
  dataSourceId: string,
  sql: string
): Promise<ActionOutcome<SqlValidateResult>> {
  try {
    const data = await callGateway<SqlValidateResult>("/api/v1/sql/validate", {
      method: "POST",
      body: { database_id: dataSourceId, sql },
    });
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

/** Reading more than `export_step_up_rows` (10,000 by default) is treated as
 * an export and requires a fresh step-up (Section 7.3) -- enforced by
 * query-gateway itself, not this app; a `maxRows` above the threshold is
 * simply refused with `STEP_UP_REQUIRED` until one is satisfied. */
export async function executeSql(
  dataSourceId: string,
  sql: string,
  maxRows: number
): Promise<ActionOutcome<SqlExecuteResult>> {
  try {
    const data = await callGateway<SqlExecuteResult>("/api/v1/sql/execute", {
      method: "POST",
      body: { database_id: dataSourceId, sql, max_rows: maxRows },
    });
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

export async function listSqlHistory(): Promise<SqlHistoryItem[]> {
  const response = await callGateway<{ items: SqlHistoryItem[]; next_cursor: string | null }>(
    "/api/v1/sql/history"
  );
  return response.items;
}
