import { listMfaFactors } from "@/features/auth/mfa-actions";
import { listInvitations, listUsers } from "@/features/admin/actions";
import { UsersWorkspace } from "@/features/admin/UsersWorkspace";
import { getSession } from "@/lib/auth/session";

/** Phase B7 DoD: inviting, granting/revoking roles, forcing session revoke,
 * resetting MFA and deactivating are all `user:manage`/`role:manage` +
 * step-up, and the server refuses to remove a tenant's last `org_admin`
 * (`LAST_ORG_ADMIN`) exactly as the API does. Zero backend changes --
 * identity-service has had this surface since Phase A10. */
export default async function UsersPage() {
  const session = await getSession();
  if (!session?.permissions.includes("user:manage")) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-text-primary">User Management</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your role does not include access to user management.
        </p>
      </div>
    );
  }

  const [users, invitations, factors] = await Promise.all([
    listUsers(),
    listInvitations(),
    listMfaFactors(),
  ]);

  return (
    <UsersWorkspace
      users={users}
      invitations={invitations}
      selfUserId={session.user_id}
      hasWebauthn={factors.some((f) => f.method === "webauthn")}
      hasAnyFactor={factors.length > 0}
    />
  );
}
