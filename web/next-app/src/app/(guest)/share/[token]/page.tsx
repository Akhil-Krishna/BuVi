import { getSnapshot } from "@/features/guest-share/actions";
import { SnapshotGrid } from "@/features/guest-share/SnapshotGrid";

/** Stitch "Executive Revenue & Margin Synthesis -- Shared Read-Only View"
 * (Section 5.2): zero app chrome, unauthenticated. No `(guest)/layout.tsx` is
 * added on purpose -- the root layout's `<html>/<body>` is all this route
 * gets, so there is no `TopNav`, no sign-in redirect, nothing to strip. */
export default async function GuestSharePage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  const snapshot = await getSnapshot(token);

  if (!snapshot) {
    return (
      <main className="mx-auto flex min-h-screen w-full max-w-lg flex-col items-center justify-center px-4 text-center">
        <h1 className="text-lg font-semibold text-text-primary">This link is no longer available.</h1>
        <p className="mt-2 text-sm text-text-secondary">
          It may have expired, been revoked, or never existed.
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8">
      <h1 className="text-xl font-semibold text-text-primary">{snapshot.name}</h1>
      <p className="mt-1 text-sm text-text-secondary">
        Shared, read-only view -- expires {new Date(snapshot.expires_at).toLocaleString()}
      </p>
      <SnapshotGrid tiles={snapshot.tiles} />
    </main>
  );
}
