import { listMfaFactors } from "@/features/auth/mfa-actions";
import { listWebhooks } from "@/features/webhooks/actions";
import { WebhooksWorkspace } from "@/features/webhooks/WebhooksWorkspace";
import { getSession } from "@/lib/auth/session";

/** Phase B8 DoD: `org_admin`-only (checked server-side by `require_org_admin`,
 * a role check, not a permission -- `user:manage` is used here only as this
 * app's nav/page gate, since only `org_admin` holds it per Section 7.1).
 * Create and disable both need step-up; the signing secret is shown exactly
 * once, at creation, and never again -- notification-service never stores it. */
export default async function WebhooksPage() {
  const session = await getSession();
  if (!session?.permissions.includes("user:manage")) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-text-primary">Webhooks</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your role does not include access to webhook administration.
        </p>
      </div>
    );
  }

  const [webhooks, factors] = await Promise.all([listWebhooks(), listMfaFactors()]);

  return (
    <WebhooksWorkspace
      webhooks={webhooks}
      hasWebauthn={factors.some((f) => f.method === "webauthn")}
      hasAnyFactor={factors.length > 0}
    />
  );
}
