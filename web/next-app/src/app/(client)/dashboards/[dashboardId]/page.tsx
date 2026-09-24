import Link from "next/link";
import { notFound } from "next/navigation";
import { listMfaFactors } from "@/features/auth/mfa-actions";
import { getDashboard, listShareLinks } from "@/features/dashboards/actions";
import { SharePanel } from "@/features/dashboards/SharePanel";
import { TileGrid } from "@/features/dashboards/TileGrid";
import { getSession } from "@/lib/auth/session";

/** Stitch "Executive Revenue & Margin Synthesis -- Dashboard View" (Section
 * 5.2): tiles plus share-link management. The mock's filter bar, date-range
 * picker, and per-tile "cache freshness" annotations have no backing
 * endpoint (Section 9's `DashboardDetailResponse` is just tiles) -- same
 * caveat as the Dashboards grid page. */
export default async function DashboardDetailPage({
  params,
}: {
  params: Promise<{ dashboardId: string }>;
}) {
  const { dashboardId } = await params;
  const session = await getSession();
  if (!session?.permissions.includes("dashboard:read")) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-text-primary">Dashboard</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your role does not include access to dashboards.
        </p>
      </div>
    );
  }

  const dashboard = await getDashboard(dashboardId);
  if (!dashboard) notFound();

  const canShare = session.permissions.includes("dashboard:share");
  const [links, factors] = await Promise.all([
    canShare ? listShareLinks(dashboardId) : Promise.resolve([]),
    canShare ? listMfaFactors() : Promise.resolve([]),
  ]);
  const hasWebauthn = factors.some((factor) => factor.method === "webauthn");

  return (
    <div>
      <p className="text-sm text-text-secondary">
        <Link href="/dashboards" className="hover:underline">
          Dashboards
        </Link>{" "}
        / {dashboard.name}
      </p>
      <h1 className="mt-1 text-xl font-semibold text-text-primary">{dashboard.name}</h1>

      <TileGrid tiles={dashboard.tiles} />

      {canShare && (
        <section className="mt-10">
          <h2 className="text-base font-semibold text-text-primary">Share</h2>
          <p className="mt-1 mb-3 text-sm text-text-secondary">
            Anyone with the link sees a read-only snapshot -- no sign-in required.
          </p>
          <SharePanel
            dashboardId={dashboardId}
            links={links}
            hasWebauthn={hasWebauthn}
            hasAnyFactor={factors.length > 0}
          />
        </section>
      )}
    </div>
  );
}
