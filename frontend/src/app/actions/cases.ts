"use server";

import { refresh } from "next/cache";

import { ApiError, api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

// Every action re-authorises through the API (the session cookie travels with each call); nothing here trusts the
// browser. On success the page is re-rendered with refresh(); on refusal the API's own message is returned.

export type ActionResult = { ok?: string; error?: string };

type CaseStatus = components["schemas"]["CaseStatus"];
type ClosureReason = components["schemas"]["ClosureReason"];
type Decision = components["schemas"]["ApprovalDecisionType"];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function run(work: () => Promise<string>): Promise<ActionResult> {
  try {
    const ok = await work();
    refresh();
    return { ok };
  } catch (error) {
    if (error instanceof ApiError) return { error: error.detail };
    throw error; // redirects (expired session) and genuine failures propagate
  }
}

function field(form: FormData, name: string): string {
  return String(form.get(name) ?? "").trim();
}

function caseId(form: FormData): string {
  const id = field(form, "case_id");
  if (!UUID.test(id)) throw new ApiError(400, "invalid case");
  return id;
}

export async function assignCase(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    const assignee = field(form, "assignee_id");
    if (!UUID.test(assignee)) throw new ApiError(422, "Choose someone to assign.");
    unwrap(await (await api()).POST("/cases/{case_id}/assign", {
      params: { path: { case_id: caseId(form) } }, body: { assignee_id: assignee },
    }));
    return "Case assigned.";
  });
}

export async function transitionCase(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    const reason = field(form, "closure_reason");
    unwrap(await (await api()).POST("/cases/{case_id}/transition", {
      params: { path: { case_id: caseId(form) } },
      body: {
        to_status: field(form, "to_status") as CaseStatus,
        comment: field(form, "comment") || null,
        closure_reason: (reason || null) as ClosureReason | null,
      },
    }));
    return "Case status updated.";
  });
}

export async function addNote(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    const text = field(form, "text");
    if (!text) throw new ApiError(422, "Write a note first.");
    unwrap(await (await api()).POST("/cases/{case_id}/notes", {
      params: { path: { case_id: caseId(form) } }, body: { text },
    }));
    return "Note added.";
  });
}

export async function startInvestigation(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    unwrap(await (await api()).POST("/cases/{case_id}/investigate", { params: { path: { case_id: caseId(form) } } }));
    return "Investigation started. The agents usually take about a minute.";
  });
}

export async function decide(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    const requestId = field(form, "request_id");
    if (!UUID.test(requestId)) throw new ApiError(400, "invalid approval request");
    const decision = field(form, "decision") as Decision;
    unwrap(await (await api()).POST("/approvals/{request_id}/decision", {
      params: { path: { request_id: requestId } },
      body: { decision, comment: field(form, "comment") || null },
    }));
    return decision === "approve" ? "Approved. The resolution is being carried out." : "Decision recorded.";
  });
}
