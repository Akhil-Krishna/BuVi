/** Mirrors `mcp_gateway.api.v1.schemas` (Section 14, Phase A9/A10). */
export type ToolClass = "read_metadata" | "read_data" | "external_read" | "write" | "admin";
export type ServerStatus = "pending_approval" | "approved" | "disabled" | "rejected";

export type Grant = {
  id: string;
  grantee_role: string | null;
  grantee_user_id: string | null;
  granted_by: string;
  granted_at: string;
};

export type Tool = {
  id: string;
  name: string;
  tool_class: ToolClass;
  default_policy: "allow" | "require_grant" | "deny";
  grants: Grant[];
};

export type Server = {
  id: string;
  name: string;
  endpoint_url: string;
  status: ServerStatus;
  has_auth_token: boolean;
  created_by: string;
  approved_by: string | null;
  created_at: string;
};

export type ServerDetail = Server & { tools: Tool[] };

export type InvokeResult = {
  invocation_id: string;
  is_error: boolean;
  content: { type: "text"; text: string }[];
  structured_content: Record<string, unknown> | null;
  dropped_blocks: number;
};
