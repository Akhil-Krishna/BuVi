"use server";

import { revalidatePath } from "next/cache";
import { callGateway, GatewayError } from "@/lib/api/gateway";
import type { Dimension, Metric, MetricGrain } from "./types";

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

export async function listMetrics(): Promise<Metric[]> {
  const response = await callGateway<{ items: Metric[]; next_cursor: string | null }>(
    "/api/v1/semantic/metrics"
  );
  return response.items;
}

/** `expression` is composed client-side from the structured builder
 * (aggregation + column, Section 8.3's `AGG([DISTINCT] column)` grammar) --
 * never free-typed SQL. semantic-service re-validates it against the
 * catalog regardless (not PII, numeric where required, table visible to
 * agents), so this is a UX guardrail, not the security boundary. */
export async function createMetric(input: {
  name: string;
  description: string | null;
  expression: string;
  baseTableId: string;
  defaultGrain: MetricGrain | null;
  synonyms: string[];
}): Promise<ActionOutcome<Metric>> {
  try {
    const data = await callGateway<Metric>("/api/v1/semantic/metrics", {
      method: "POST",
      body: {
        name: input.name,
        description: input.description,
        expression: input.expression,
        base_table_id: input.baseTableId,
        default_grain: input.defaultGrain,
        synonyms: input.synonyms,
      },
    });
    revalidatePath("/semantic");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

/** draft -> approved. Only an approved metric reaches the Flow (Section 8.3) --
 * the same `semantic:manage` permission that creates a metric can also
 * approve it today (no maker-checker separation; four-eyes approval as a
 * tenant policy is post-GA, ADR 0013). */
export async function approveMetric(id: string): Promise<ActionOutcome<Metric>> {
  try {
    const data = await callGateway<Metric>(`/api/v1/semantic/metrics/${id}/approve`, {
      method: "POST",
    });
    revalidatePath("/semantic");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

/** approved -> deprecated. `next_status` (metric_expression.py) allows only
 * draft->approved->deprecated, never a reverse transition. */
export async function deprecateMetric(id: string): Promise<ActionOutcome<Metric>> {
  try {
    const data = await callGateway<Metric>(`/api/v1/semantic/metrics/${id}/deprecate`, {
      method: "POST",
    });
    revalidatePath("/semantic");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

export async function listDimensions(): Promise<Dimension[]> {
  const response = await callGateway<{ items: Dimension[]; next_cursor: string | null }>(
    "/api/v1/semantic/dimensions"
  );
  return response.items;
}

export async function createDimension(input: {
  name: string;
  columnId: string;
  synonyms: string[];
}): Promise<ActionOutcome<Dimension>> {
  try {
    const data = await callGateway<Dimension>("/api/v1/semantic/dimensions", {
      method: "POST",
      body: { name: input.name, column_id: input.columnId, synonyms: input.synonyms },
    });
    revalidatePath("/semantic");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}
