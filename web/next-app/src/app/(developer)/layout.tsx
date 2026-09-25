import type { ReactNode } from "react";
import { redirect } from "next/navigation";
import { TopNav } from "@/components/layout/TopNav";
import { getSession } from "@/lib/auth/session";

/** Shared shell for the developer/admin workspace (Section 4.2's `(developer)`
 * route group) -- same guard as `(client)/layout.tsx`, factored out once B3
 * needed it alongside `(client)`'s own two pages. */
export default async function DeveloperLayout({ children }: { children: ReactNode }) {
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
