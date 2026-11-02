"use server";

import { refresh } from "next/cache";

import {
  ApprovalDecisionType, ApprovalDecisionTypeSchema, CaseStatus, CaseStatusSchema, ClosureReason, ClosureReasonSchema,
} from "@/gen/finrecon/v1/enums_pb";
import { ApiError } from "@/lib/api/client";
import { enumFrom } from "@/lib/format";
import { rpc } from "@/lib/rpc";

// Every action re-authorises through the API's RPC services (the session token travels with each call); nothing
// here trusts the browser. On success the page is re-rendered with refresh(); on refusal the API's message returns.

export type ActionResult = { ok?: string; error?: string };

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
    await (await rpc()).cases.assignCase({ caseId: caseId(form), assigneeId: assignee });
    return "Case assigned.";
  });
}

export async function transitionCase(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    const toStatus = enumFrom<CaseStatus>(CaseStatusSchema, field(form, "to_status"));
    if (toStatus === undefined) throw new ApiError(422, "Choose a status.");
    await (await rpc()).cases.transitionCase({
      caseId: caseId(form),
      toStatus,
      comment: field(form, "comment") || undefined,
      closureReason: enumFrom<ClosureReason>(ClosureReasonSchema, field(form, "closure_reason")),
    });
    return "Case status updated.";
  });
}

export async function addNote(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    const text = field(form, "text");
    if (!text) throw new ApiError(422, "Write a note first.");
    await (await rpc()).cases.addNote({ caseId: caseId(form), text });
    return "Note added.";
  });
}

export async function startInvestigation(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    await (await rpc()).cases.startInvestigation({ caseId: caseId(form) });
    return "Investigation started. The agents usually take about a minute.";
  });
}

export async function decide(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    const requestId = field(form, "request_id");
    if (!UUID.test(requestId)) throw new ApiError(400, "invalid approval request");
    const decision = enumFrom<ApprovalDecisionType>(ApprovalDecisionTypeSchema, field(form, "decision"));
    if (decision === undefined) throw new ApiError(422, "Choose a decision.");
    await (await rpc()).approvals.decideApproval({ requestId, decision, comment: field(form, "comment") || undefined });
    return decision === ApprovalDecisionType.APPROVE ? "Approved. The resolution is being carried out." : "Decision recorded.";
  });
}
