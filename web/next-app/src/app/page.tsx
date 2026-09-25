import { redirect } from "next/navigation";
import { TopNav } from "@/components/layout/TopNav";
import { listNotifications } from "@/features/notifications/actions";
import { getSession } from "@/lib/auth/session";

/**
 * Phase B1's own landing page -- proves the authenticated shell (real session,
 * role-scoped top nav) end to end. The `(client)`/`(developer)`/`(admin)`
 * route groups and their real pages are Phase B2 onward (Section 31); this
 * page is deliberately minimal on purpose, not an oversight.
 */
export default async function HomePage() {
  const session = await getSession();
  if (!session) {
    redirect("/login");
  }
  if (session.mfa_enabled && !session.mfa_verified) {
    redirect("/mfa");
  }
  const { items, unread } = await listNotifications();

  return (
    <div className="flex min-h-screen flex-col bg-bg-subtle">
      <TopNav session={session} notifications={items} unreadCount={unread} />
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8">
        <h1 className="text-xl font-semibold text-text-primary">
          Signed in as {session.display_name}
        </h1>
        <p className="mt-2 text-sm text-text-secondary">
          Roles: {session.roles.join(", ") || "none"}
        </p>
        <p className="mt-6 text-sm text-text-secondary">
          Chat, dashboards, and the developer and admin workspaces arrive in Phase B2 onward.
        </p>
      </main>
    </div>
  );
}
