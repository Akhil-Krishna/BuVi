"use server";

import { revalidatePath } from "next/cache";
import { callGateway, GatewayError } from "@/lib/api/gateway";
import type { Dashboard, DashboardDetail, ShareLink, ShareLinkCreated, Tile } from "./types";

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

export async function listDashboards(): Promise<Dashboard[]> {
  const response = await callGateway<{ items: Dashboard[]; next_cursor: string | null }>(
    "/api/v1/dashboards"
  );
  return response.items;
}

export async function getDashboard(id: string): Promise<DashboardDetail | null> {
  try {
    return await callGateway<DashboardDetail>(`/api/v1/dashboards/${id}`);
  } catch (error) {
    if (error instanceof GatewayError && error.status === 404) return null;
    throw error;
  }
}

export async function createDashboard(name: string): Promise<ActionOutcome<Dashboard>> {
  try {
    const data = await callGateway<Dashboard>("/api/v1/dashboards", {
      method: "POST",
      body: { name },
    });
    revalidatePath("/dashboards");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

/** Section 32 Step D: pin an artifact the chat flow just produced. */
export async function pinArtifact(
  dashboardId: string,
  artifactId: string
): Promise<ActionOutcome<Tile>> {
  try {
    const data = await callGateway<Tile>(`/api/v1/dashboards/${dashboardId}/tiles`, {
      method: "POST",
      body: { artifact_id: artifactId },
    });
    revalidatePath(`/dashboards/${dashboardId}`);
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

export async function listShareLinks(dashboardId: string): Promise<ShareLink[]> {
  const response = await callGateway<{ items: ShareLink[] }>(
    `/api/v1/dashboards/${dashboardId}/share-links`
  );
  return response.items;
}

/** `dashboard:share` is a step-up operation (Section 7.3) -- a stolen session
 * cookie alone must not be able to mint a link anyone with the URL can open. */
export async function createShareLink(
  dashboardId: string,
  expiresInHours: number | null
): Promise<ActionOutcome<ShareLinkCreated>> {
  try {
    const data = await callGateway<ShareLinkCreated>(
      `/api/v1/dashboards/${dashboardId}/share-links`,
      { method: "POST", body: { expires_in_hours: expiresInHours } }
    );
    revalidatePath(`/dashboards/${dashboardId}`);
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

export async function revokeShareLink(dashboardId: string, linkId: string): Promise<ActionOutcome> {
  try {
    await callGateway(`/api/v1/dashboards/${dashboardId}/share-links/${linkId}`, {
      method: "DELETE",
    });
    revalidatePath(`/dashboards/${dashboardId}`);
    return { ok: true };
  } catch (error) {
    return failure(error);
  }
}
