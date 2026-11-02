import "server-only";

import { cache } from "react";
import { redirect } from "next/navigation";

import type { GetMeResponse } from "@/gen/finrecon/v1/identity_pb";
import { rpc } from "@/lib/rpc";
import { getToken } from "@/lib/session";

export type CurrentUser = GetMeResponse;

/**
 * Data Access Layer: the one place that establishes who is signed in. Asks the API, which reloads roles from the
 * database on every call, so a deactivated user or changed role takes effect at once. Deduplicated per request.
 */
export const getCurrentUser = cache(async (): Promise<CurrentUser> => {
  if (!(await getToken())) redirect("/login");
  return (await rpc()).identity.getMe({});
});

/** UI hint only: the API enforces every permission itself. */
export function can(user: CurrentUser, permission: string): boolean {
  return user.permissions.includes(permission);
}
