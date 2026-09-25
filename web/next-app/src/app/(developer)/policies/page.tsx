import { listMfaFactors } from "@/features/auth/mfa-actions";
import { getPolicies } from "@/features/admin/actions";
import { PoliciesPanel } from "@/features/admin/PoliciesPanel";
import { getSession } from "@/lib/auth/session";

/** Phase B7 DoD: `PATCH /admin/policies` needs `policy:manage` + step-up; no
 * dedicated Stitch screen (Section 5.2) -- follows User Management's
 * row/panel pattern. Zero backend changes -- built since Phase A10. */
export default async function PoliciesPage() {
  const session = await getSession();
  if (!session?.permissions.includes("policy:manage")) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-text-primary">Tenant Policies</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your role does not include access to tenant policies.
        </p>
      </div>
    );
  }

  const [policies, factors] = await Promise.all([getPolicies(), listMfaFactors()]);

  return (
    <PoliciesPanel
      policies={policies}
      hasWebauthn={factors.some((f) => f.method === "webauthn")}
      hasAnyFactor={factors.length > 0}
    />
  );
}
