"use server";

import { callGateway, GatewayError } from "@/lib/api/gateway";
import type { ArtifactDataResponse, ArtifactResponse } from "./types";

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

/**
 * Section 32 Step A. A chat "conversation" is created lazily on the first
 * message of a browser session -- there is no `GET /conversations` to list
 * past ones (Section 9's chat surface is create-and-post only), so this app
 * does not pretend to have a conversation history sidebar the backend cannot
 * back; the caller holds `conversationId` in memory for the tab's lifetime.
 */
export async function sendChatMessage(
  conversationId: string | null,
  content: string,
  dataSourceId: string | null
): Promise<ActionOutcome<{ runId: string; conversationId: string }>> {
  try {
    const conversation =
      conversationId ??
      (await callGateway<{ id: string }>("/api/v1/conversations", { method: "POST", body: {} })).id;
    const accepted = await callGateway<{ run_id: string; conversation_id: string }>(
      `/api/v1/conversations/${conversation}/messages`,
      { method: "POST", body: { content, data_source_id: dataSourceId } }
    );
    return { ok: true, data: { runId: accepted.run_id, conversationId: accepted.conversation_id } };
  } catch (error) {
    return failure(error);
  }
}

export async function cancelRun(runId: string): Promise<ActionOutcome> {
  try {
    await callGateway(`/api/v1/runs/${runId}/cancel`, { method: "POST" });
    return { ok: true };
  } catch (error) {
    return failure(error);
  }
}

/** Section 32 Step C. Called once a run's SSE stream reports its terminal
 * artifact so the chat panel can render the chart it just produced. */
export async function getArtifact(artifactId: string): Promise<ActionOutcome<ArtifactResponse>> {
  try {
    const data = await callGateway<ArtifactResponse>(`/api/v1/artifacts/${artifactId}`);
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

export async function getArtifactData(
  artifactId: string
): Promise<ActionOutcome<ArtifactDataResponse>> {
  try {
    const data = await callGateway<ArtifactDataResponse>(`/api/v1/artifacts/${artifactId}/data`);
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}
