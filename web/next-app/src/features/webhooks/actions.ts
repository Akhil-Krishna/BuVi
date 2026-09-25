"use server";

import { revalidatePath } from "next/cache";
import { callGateway, GatewayError } from "@/lib/api/gateway";
import type { Webhook, WebhookCreated } from "./types";

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

export async function listWebhooks(): Promise<Webhook[]> {
  const response = await callGateway<{ items: Webhook[] }>("/api/v1/admin/webhooks");
  return response.items;
}

/** `org_admin` + step-up. The signing secret is in this one response and
 * nowhere else -- notification-service never stores it, so it cannot be
 * shown again on a later fetch (Section 6.8/9's "shown once" rule). */
export async function createWebhook(
  url: string,
  eventTypes: string[]
): Promise<ActionOutcome<WebhookCreated>> {
  try {
    const data = await callGateway<WebhookCreated>("/api/v1/admin/webhooks", {
      method: "POST",
      body: { url, event_types: eventTypes },
    });
    revalidatePath("/webhooks");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

/** `org_admin` + step-up. Disables and deletes the secret. */
export async function disableWebhook(id: string): Promise<ActionOutcome> {
  try {
    await callGateway(`/api/v1/admin/webhooks/${id}`, { method: "DELETE" });
    revalidatePath("/webhooks");
    return { ok: true };
  } catch (error) {
    return failure(error);
  }
}
