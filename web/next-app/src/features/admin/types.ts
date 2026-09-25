/** Section 2's fixed tenant roles -- identity-service lists them, never creates them. */
export const TENANT_ROLES = ["client", "developer", "org_admin", "billing_admin", "auditor"] as const;
export type TenantRole = (typeof TENANT_ROLES)[number];

export type AdminUser = {
  id: string;
  tenant_id: string;
  email: string;
  display_name: string;
  status: string;
  mfa_enabled: boolean;
  roles: string[];
  last_login_at: string | null;
  created_at: string;
};

export type Invitation = {
  id: string;
  tenant_id: string;
  email: string;
  role_key: string;
  status: string;
  expires_at: string;
  created_at: string;
};

export type Policies = {
  client_can_share_dashboards: boolean;
  developer_can_manage_mcp: boolean;
  org_admin_requires_webauthn: boolean;
};

export type AuditEvent = {
  id: string;
  tenant_id: string | null;
  actor_user_id: string | null;
  actor_type: string;
  event_type: string;
  resource_type: string | null;
  resource_id: string | null;
  request_id: string | null;
  created_at: string;
};

export type TokenQuota = {
  period: "day";
  limit: number;
  used: number;
  remaining: number;
  resets_at: string;
};

export type Quotas = {
  llm_tokens: TokenQuota;
  run_token_limit: number;
};

export type LlmTokenUsage = {
  input: number;
  output: number;
  total: number;
  by_stage: Record<string, number>;
};

export type Usage = {
  start: string;
  end: string;
  llm_tokens: LlmTokenUsage;
  query_minutes: number;
  seats: number;
};
