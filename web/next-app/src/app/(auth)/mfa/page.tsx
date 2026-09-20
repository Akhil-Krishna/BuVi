import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth/session";
import { listMfaFactors } from "@/features/auth/mfa-actions";
import { MfaVerifyForm } from "@/features/auth/MfaVerifyForm";

export default async function MfaPage() {
  const session = await getSession();
  if (!session) {
    redirect("/login");
  }
  if (!session.mfa_enabled || session.mfa_verified) {
    // No factor to check, or this session already completed one -- do not
    // show a pointless prompt. (`step_up_fresh` is a separate, later concern:
    // a 5-minute freshness window for specific sensitive actions, Section
    // 7.3 -- not the one-time "does this session have MFA at all" gate.)
    redirect("/");
  }

  const factors = await listMfaFactors();
  const hasWebauthn = factors.some((factor) => factor.method === "webauthn");

  return (
    <main className="flex min-h-screen items-center justify-center bg-bg-subtle px-4">
      <MfaVerifyForm hasWebauthn={hasWebauthn} />
    </main>
  );
}
