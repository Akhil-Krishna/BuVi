"use server";

import { callGateway, GatewayError } from "@/lib/api/gateway";
import type { ArtifactDataResponse, ArtifactResponse, ChatMessage, Conversation } from "./types";

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

/** The caller's own past conversations, newest first (chat sidebar). */
export async function listConversations(): Promise<Conversation[]> {
  const response = await callGateway<{ items: Conversation[] }>("/api/v1/conversations");
  return response.items;
}

/** A past conversation's messages, in order -- clicking a sidebar item loads these. */
export async function getConversationMessages(conversationId: string): Promise<ChatMessage[]> {
  const response = await callGateway<{ items: ChatMessage[] }>(
    `/api/v1/conversations/${conversationId}/messages`
  );
  return response.items;
}

/** Section 32 Step A. A chat "conversation" is created lazily on the first message of a new
 * thread; picking a past one from the sidebar reuses its id instead (`sendChatMessage`'s first
 * argument). */
export async function sendChatMessage(
  conversationId: string | null,
  content: string,
  dataSourceId: string | null
): Promise<ActionOutcome<{ runId: string; conversationId: string }>> {
  try {
    const conversation =
      conversationId ??
      (
        await callGateway<{ id: string }>("/api/v1/conversations", {
          method: "POST",
          // Auto-titled from the opening message, the way Claude/ChatGPT title a new chat --
          // otherwise every sidebar entry reads "Untitled".
          body: { title: content.slice(0, 60) },
        })
      ).id;
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
