import type { ReactNode } from "react";
import { redirect } from "next/navigation";
import { TopNav } from "@/components/layout/TopNav";
import { getSession } from "@/lib/auth/session";

/** Shared shell for Section 32's client-role journey (chat, dashboards) --
 * the same auth guard `page.tsx` and `account/page.tsx` each inline, factored
 * out now that a third and fourth page need it (Section 4.2's `(client)`
 * route group). */
export default async function ClientLayout({ children }: { children: ReactNode }) {
  const session = await getSession();
  if (!session) {
    redirect("/login");
  }
  if (session.mfa_enabled && !session.mfa_verified) {
    redirect("/mfa");
  }

  return (
    <div className="flex min-h-screen flex-col bg-bg-subtle">
      <TopNav session={session} />
      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col px-4 py-8">{children}</main>
    </div>
  );
}
