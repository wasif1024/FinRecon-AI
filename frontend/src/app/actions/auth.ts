"use server";

import { redirect } from "next/navigation";

import { api } from "@/lib/api/client";
import { clearToken, setToken } from "@/lib/session";

export type LoginState = { error?: string; tenant?: string; email?: string };

export async function login(_previous: LoginState, form: FormData): Promise<LoginState> {
  const tenant = String(form.get("tenant") ?? "").trim();
  const email = String(form.get("email") ?? "").trim();
  const password = String(form.get("password") ?? "");
  if (!tenant || !email || !password) return { error: "Enter your organisation, email and password.", tenant, email };

  const { data, response } = await (await api()).POST("/auth/login", { body: { tenant, email, password } });
  if (response.status === 429) {
    return { error: "Too many failed attempts. Try again in 15 minutes.", tenant, email };
  }
  if (!response.ok || !data) return { error: "Those details didn't match an account.", tenant, email };

  await setToken(data.access_token, new Date(data.expires_at));
  redirect(safeNext(form.get("next")));
}

export async function logout(): Promise<void> {
  await clearToken();
  redirect("/login");
}

/** Only same-site paths: "/cases/1" yes; "//evil.example" or "https://evil.example" no (open redirect). */
function safeNext(value: FormDataEntryValue | null): string {
  const next = typeof value === "string" ? value : "";
  return next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\") ? next : "/";
}
