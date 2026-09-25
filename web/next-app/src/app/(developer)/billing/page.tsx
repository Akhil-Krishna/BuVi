import { getQuotas, getUsage } from "@/features/admin/actions";
import { BillingPanel } from "@/features/admin/BillingPanel";
import { getSession } from "@/lib/auth/session";

/** Phase B7 DoD: read-only, `billing:read`. Renders only what `GET
 * /billing/usage` / `GET /billing/quotas` back -- Section 5.2's caveat: the
 * mock's invoice-reconciliation and spend-cap controls are not wired to any
 * action, since `POST /billing/subscription` is a documented `501` stub. */
export default async function BillingPage() {
  const session = await getSession();
  if (!session?.permissions.includes("billing:read")) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-text-primary">Usage & Quotas</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your role does not include access to billing.
        </p>
      </div>
    );
  }

  const [usage, quotas] = await Promise.all([getUsage(), getQuotas()]);
  return <BillingPanel usage={usage} quotas={quotas} />;
}
