"use server";

import { revalidatePath } from "next/cache";
import { callGateway } from "@/lib/api/gateway";
import type { Notification } from "./types";

export async function listNotifications(): Promise<{ items: Notification[]; unread: number }> {
  const response = await callGateway<{ items: Notification[]; unread: number; next_cursor: string | null }>(
    "/api/v1/me/notifications"
  );
  return { items: response.items, unread: response.unread };
}

export async function markNotificationRead(id: string): Promise<void> {
  await callGateway(`/api/v1/me/notifications/${id}/read`, { method: "POST" });
  revalidatePath("/", "layout");
}
