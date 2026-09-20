"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { GATEWAY_URL, SESSION_COOKIE } from "@/lib/config";

/** Section 9: `POST /auth/logout` revokes the session at identity-service and,
 * transitively, at the IdP. This app clears its own cookie regardless of the
 * upstream result -- a user who asked to sign out must never be left looking
 * signed in locally because of a transient backend error. */
export async function logout(): Promise<never> {
  const store = await cookies();
  const token = store.get(SESSION_COOKIE)?.value;

  if (token) {
    await fetch(new URL("/api/v1/auth/logout", GATEWAY_URL), {
      method: "POST",
      headers: { cookie: `${SESSION_COOKIE}=${token}` },
    }).catch(() => undefined);
  }

  store.delete(SESSION_COOKIE);
  redirect("/login");
}
