import { logout } from "@/features/auth/logout";
import type { Session } from "@/lib/auth/session";
import { navItemsFor } from "./nav-items";

export function TopNav({ session }: { session: Session }) {
  const items = navItemsFor(session);

  return (
    <header className="flex h-12 items-center justify-between border-b border-border bg-bg px-4">
      <nav className="flex items-center gap-4">
        <span className="text-sm font-semibold text-text-primary">BuVi</span>
        {items.map((item) => (
          <a
            key={item.href}
            href={item.href}
            className="text-sm font-medium text-text-secondary hover:text-text-primary"
          >
            {item.label}
          </a>
        ))}
      </nav>
      <div className="flex items-center gap-3">
        <a href="/account" className="text-sm text-text-secondary hover:text-text-primary">
          {session.email}
        </a>
        <form action={logout}>
          <button
            type="submit"
            className="rounded-sm border border-border px-3 py-1 text-sm text-text-primary hover:bg-bg-subtle"
          >
            Sign out
          </button>
        </form>
      </div>
    </header>
  );
}
