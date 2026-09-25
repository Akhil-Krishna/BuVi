"use server";

import { revalidatePath } from "next/cache";
import { callGateway, GatewayError } from "@/lib/api/gateway";
import type { InvokeResult, Server, ServerDetail, ToolClass } from "./types";

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

export async function listServers(): Promise<Server[]> {
  const response = await callGateway<{ items: Server[]; next_cursor: string | null }>(
    "/api/v1/mcp/servers"
  );
  return response.items;
}

export async function getServer(id: string): Promise<ActionOutcome<ServerDetail>> {
  try {
    const data = await callGateway<ServerDetail>(`/api/v1/mcp/servers/${id}`);
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

export async function registerServer(input: {
  name: string;
  endpointUrl: string;
  authToken: string | null;
  tools: { name: string; toolClass: ToolClass }[];
}): Promise<ActionOutcome<ServerDetail>> {
  try {
    const data = await callGateway<ServerDetail>("/api/v1/mcp/servers", {
      method: "POST",
      body: {
        name: input.name,
        endpoint_url: input.endpointUrl,
        auth_token: input.authToken,
        tools: input.tools.map((t) => ({ name: t.name, tool_class: t.toolClass })),
      },
    });
    revalidatePath("/mcp");
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

/** `org_admin` + step-up (Section 7.3): approving a server makes its
 * declared tools reachable, so a stolen session cookie alone must not. */
export async function approveServer(id: string): Promise<ActionOutcome> {
  try {
    await callGateway(`/api/v1/mcp/servers/${id}/approve`, { method: "POST" });
    revalidatePath("/mcp");
    return { ok: true };
  } catch (error) {
    return failure(error);
  }
}

export async function rejectServer(id: string): Promise<ActionOutcome> {
  try {
    await callGateway(`/api/v1/mcp/servers/${id}/reject`, { method: "POST" });
    revalidatePath("/mcp");
    return { ok: true };
  } catch (error) {
    return failure(error);
  }
}

export async function disableServer(id: string): Promise<ActionOutcome> {
  try {
    await callGateway(`/api/v1/mcp/servers/${id}/disable`, { method: "POST" });
    revalidatePath("/mcp");
    return { ok: true };
  } catch (error) {
    return failure(error);
  }
}

export async function grantTool(
  serverId: string,
  tool: string,
  target: { role: string } | { userId: string }
): Promise<ActionOutcome> {
  try {
    await callGateway(`/api/v1/mcp/servers/${serverId}/tools/${tool}/grants`, {
      method: "POST",
      body:
        "role" in target
          ? { grantee_role: target.role, grantee_user_id: null }
          : { grantee_role: null, grantee_user_id: target.userId },
    });
    revalidatePath("/mcp");
    return { ok: true };
  } catch (error) {
    return failure(error);
  }
}

export async function revokeToolGrant(
  serverId: string,
  tool: string,
  grantId: string
): Promise<ActionOutcome> {
  try {
    await callGateway(`/api/v1/mcp/servers/${serverId}/tools/${tool}/grants/${grantId}`, {
      method: "DELETE",
    });
    revalidatePath("/mcp");
    return { ok: true };
  } catch (error) {
    return failure(error);
  }
}

/** `write`/`admin` tools need a fresh step-up at *every* invocation, not
 * just when granted (tool_policy.py's `STEP_UP_CLASSES`). */
export async function invokeTool(
  serverId: string,
  tool: string,
  args: Record<string, unknown>
): Promise<ActionOutcome<InvokeResult>> {
  try {
    const data = await callGateway<InvokeResult>(
      `/api/v1/mcp/servers/${serverId}/tools/${tool}/invoke`,
      { method: "POST", body: { arguments: args } }
    );
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}
