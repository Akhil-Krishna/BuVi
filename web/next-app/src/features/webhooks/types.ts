/** Section 15's egress allow-list -- what may leave the platform as a webhook. */
export const WEBHOOK_EVENT_TYPES = [
  "dashboard.tile.pinned",
  "metadata.sync.completed",
  "mcp.invocation.denied",
] as const;

export type Webhook = {
  id: string;
  url: string;
  event_types: string[];
  status: string;
  created_by: string;
  created_at: string;
};

export type WebhookCreated = Webhook & { signing_secret: string };
