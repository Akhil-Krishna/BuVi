import { beginLogin } from "@/features/auth/actions";

/**
 * Stitch reference screen: "SSO Login" (spec Section 5.2). Enterprise BI, not
 * a marketing page -- a plain bordered panel on the subtle page background,
 * no illustration, no gradient, no shadow on the resting card.
 */
export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-bg-subtle px-4">
      <div className="w-full max-w-sm rounded-md border border-border bg-bg p-8">
        <div className="mb-8 text-center">
          <p className="text-lg font-semibold text-text-primary">BuVi</p>
          <p className="mt-1 text-sm text-text-secondary">Business Visualization</p>
        </div>
        <form action={beginLogin}>
          <button
            type="submit"
            className="w-full rounded-sm bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-hover"
          >
            Continue with SSO
          </button>
        </form>
        <p className="mt-6 text-center text-xs text-text-secondary">
          Sign in with your organization&apos;s identity provider.
        </p>
      </div>
    </main>
  );
}
