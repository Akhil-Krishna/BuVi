import { listAuditEvents } from "@/features/admin/actions";
import { AuditPanel } from "@/features/admin/AuditPanel";
import { getSession } from "@/lib/auth/session";

/** Phase B7 DoD: read-only, `audit:read`. Zero backend changes -- built since
 * Phase A1/A10. */
export default async function AuditPage() {
  const session = await getSession();
  if (!session?.permissions.includes("audit:read")) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-text-primary">Audit Log</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your role does not include access to the audit log.
        </p>
      </div>
    );
  }

  const events = await listAuditEvents();
  return <AuditPanel events={events} />;
}
