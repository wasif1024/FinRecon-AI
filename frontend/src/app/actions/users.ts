"use server";

import { refresh } from "next/cache";

import { ApiError } from "@/lib/api/client";
import { rpc } from "@/lib/rpc";
import type { ActionResult } from "./cases";

// User administration (admins). The API enforces every rule (permission, tenant, the lockout guards) and audits
// each change; these actions only collect the form and report the outcome.

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ROLES = new Set(["analyst", "manager", "admin", "auditor"]);

async function run(work: () => Promise<string>): Promise<ActionResult> {
  try {
    const ok = await work();
    refresh();
    return { ok };
  } catch (error) {
    if (error instanceof ApiError) return { error: error.detail };
    throw error;
  }
}

function field(form: FormData, name: string): string {
  return String(form.get(name) ?? "").trim();
}

function userId(form: FormData): string {
  const id = field(form, "user_id");
  if (!UUID.test(id)) throw new ApiError(400, "invalid user");
  return id;
}

function roles(form: FormData): string[] {
  const chosen = form.getAll("roles").map(String).filter((r) => ROLES.has(r));
  if (chosen.length === 0) throw new ApiError(422, "Choose at least one role.");
  return chosen;
}

export async function createUser(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    const { account } = await (await rpc()).users.createUser({
      email: field(form, "email"), fullName: field(form, "full_name"), roles: roles(form),
      password: String(form.get("password") ?? ""),
    });
    return `${account?.fullName ?? "User"} can now sign in.`;
  });
}

export async function updateRoles(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    await (await rpc()).users.updateUser({ userId: userId(form), roles: { roles: roles(form) } });
    return "Roles updated. They apply from the user's next request.";
  });
}

export async function setActive(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    const active = field(form, "active") === "true";
    await (await rpc()).users.updateUser({ userId: userId(form), active });
    return active ? "User reactivated." : "User deactivated: signed out everywhere at their next request.";
  });
}

export async function setPassword(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    await (await rpc()).users.setPassword({ userId: userId(form), password: String(form.get("password") ?? "") });
    return "Password set. Share it with the user over a separate channel.";
  });
}
