import "server-only";

import { cookies } from "next/headers";

// The FastAPI access token lives only in this httpOnly cookie. Browser JavaScript never sees it; the Next.js
// server reads it and forwards it to the API (backend-for-frontend).
const COOKIE = "finrecon_session";

export async function getToken(): Promise<string | undefined> {
  return (await cookies()).get(COOKIE)?.value;
}

/** Call only from a Server Function or Route Handler (cookies cannot be set while rendering). */
export async function setToken(token: string, expiresAt: Date): Promise<void> {
  (await cookies()).set(COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    expires: expiresAt,
  });
}

/** Call only from a Server Function or Route Handler. */
export async function clearToken(): Promise<void> {
  (await cookies()).delete(COOKIE);
}
