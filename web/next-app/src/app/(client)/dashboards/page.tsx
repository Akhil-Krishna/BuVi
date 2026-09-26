import Link from "next/link";
import { NewDashboardForm } from "@/features/dashboards/NewDashboardForm";
import { listDashboards } from "@/features/dashboards/actions";
import { getSession } from "@/lib/auth/session";
import { formatDateTime } from "@/lib/format";

/** Stitch "Dashboards" grid (Section 5.2). The mock shows certification
 * badges, dataset counts, and cache-freshness stats nothing in
 * `DashboardListResponse` backs (Section 9) -- same class of gap as the
 * Usage & Quotas caveat (ADR 0017): this table renders only the fields the
 * backend actually returns, not the mock's full chrome. */
export default async function DashboardsPage() {
  const session = await getSession();
  if (!session?.permissions.includes("dashboard:read")) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-text-primary">Dashboards</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your role does not include access to dashboards.
        </p>
      </div>
    );
  }

  const dashboards = await listDashboards();
  const canCreate = session.permissions.includes("dashboard:pin");

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-text-primary">Dashboards</h1>
        {canCreate && <NewDashboardForm />}
      </div>

      {dashboards.length === 0 ? (
        <p className="mt-6 text-sm text-text-secondary">
          No dashboards yet. Pin a chart from Chat, or create one above.
        </p>
      ) : (
        <div className="mt-6 overflow-x-auto border border-border">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-border bg-bg-subtle">
                <th className="px-3 py-2 font-semibold text-text-secondary">Name</th>
                <th className="px-3 py-2 font-semibold text-text-secondary">Visibility</th>
                <th className="px-3 py-2 font-semibold text-text-secondary">Owner</th>
                <th className="px-3 py-2 font-semibold text-text-secondary">Last modified</th>
              </tr>
            </thead>
            <tbody>
              {dashboards.map((dashboard, index) => (
                <tr
                  key={dashboard.id}
                  className={`border-b border-border last:border-b-0 hover:bg-row-hover ${
                    index % 2 === 1 ? "bg-bg-subtle" : "bg-bg"
                  }`}
                >
                  <td className="px-3 py-2">
                    <Link
                      href={`/dashboards/${dashboard.id}`}
                      className="text-primary hover:underline"
                    >
                      {dashboard.name}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-text-secondary">{dashboard.visibility}</td>
                  <td className="px-3 py-2 text-text-secondary">
                    {dashboard.is_owner ? "You" : "Shared with tenant"}
                  </td>
                  <td className="px-3 py-2 text-text-secondary">
                    {formatDateTime(dashboard.updated_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
