import "server-only";

import createClient, { type Middleware } from "openapi-fetch";
import { redirect } from "next/navigation";
import { connection } from "next/server";

import { getToken } from "@/lib/session";
import type { paths } from "./schema";

/** Raised for any non-2xx answer the caller should handle (403, 404, 409, 422 ...). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
  }
}

const authHeader: Middleware = {
  async onRequest({ request }) {
    const token = await getToken();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
};

function baseUrl(): string {
  const url = process.env.API_URL;
  if (!url) throw new Error("API_URL is not set");
  return url;
}

/**
 * Typed FastAPI client for the current request, authenticated with the session cookie. Tenant data is always
 * fetched at request time and never prerendered or cached on the server, so this marks the caller as dynamic first.
 */
export async function api() {
  await connection();
  const client = createClient<paths>({ baseUrl: baseUrl(), cache: "no-store" });
  client.use(authHeader);
  return client;
}

type Result<T> = { data?: T; error?: unknown; response: Response };

/**
 * The response body, or: a redirect to login on 401 (missing or expired session), or an ApiError carrying the
 * API's own message. Every page and action goes through this, so an expired session is handled in one place.
 */
export function unwrap<T>(result: Result<T>): T {
  const { data, error, response } = result;
  if (response.status === 401) redirect("/login?expired=1");
  if (!response.ok || data === undefined) throw new ApiError(response.status, detailOf(error, response));
  return data;
}

function detailOf(error: unknown, response: Response): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? String(d)).join("; ");
  }
  return `${response.status} ${response.statusText}`;
}
