import { redirect } from "next/navigation";
import { TopNav } from "@/components/layout/TopNav";
import { MfaPanel } from "@/features/account/MfaPanel";
import { SessionList } from "@/features/account/SessionList";
import { listSessions } from "@/features/account/session-actions";
import { listMfaFactors } from "@/features/auth/mfa-actions";
import { getSession } from "@/lib/auth/session";

/**
 * Personal account settings: the caller's own sessions (Section 6.9) and MFA
 * factors (Section 6.6). Every endpoint behind this page is scoped to the
 * caller by identity-service itself -- no user id travels in any request here,
 * so there is nothing to tamper with.
 *
 * Not under a `(client)`/`(developer)`/`(admin)` route group on purpose: this
 * page belongs to every authenticated role, and Section 4.2 has no group that
 * means "any signed-in user".
 */
export default async function AccountPage() {
  const session = await getSession();
  if (!session) {
    redirect("/login");
  }
  if (session.mfa_enabled && !session.mfa_verified) {
    redirect("/mfa");
  }

  const [sessions, factors] = await Promise.all([listSessions(), listMfaFactors()]);

  return (
    <div className="flex min-h-screen flex-col bg-bg-subtle">
      <TopNav session={session} />
      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-8">
        <h1 className="text-xl font-semibold text-text-primary">Account</h1>
        <p className="mt-1 text-sm text-text-secondary">
          {session.display_name} · {session.email}
        </p>

        <section className="mt-8">
          <h2 className="text-base font-semibold text-text-primary">Two-factor authentication</h2>
          <p className="mt-1 mb-3 text-sm text-text-secondary">
            Required for sensitive actions such as changing roles or connection credentials.
          </p>
          <MfaPanel factors={factors} />
        </section>

        <section className="mt-10">
          <h2 className="text-base font-semibold text-text-primary">Active sessions</h2>
          <p className="mt-1 mb-3 text-sm text-text-secondary">
            Revoking a session signs that browser out immediately.
          </p>
          <SessionList sessions={sessions} />
        </section>
      </main>
    </div>
  );
}
