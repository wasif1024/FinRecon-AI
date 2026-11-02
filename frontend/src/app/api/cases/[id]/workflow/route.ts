import { NextResponse } from "next/server";

import { ApiError } from "@/lib/api/client";
import { rpc } from "@/lib/rpc";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Workflow status for live progress polling. Calls the API with the session cookie; the token stays server-side. */
export async function GET(_: Request, { params }: RouteContext<"/api/cases/[id]/workflow">) {
  const { id } = await params;
  if (!UUID.test(id)) return NextResponse.json({ detail: "not found" }, { status: 404 });
  try {
    const { workflow } = await (await rpc()).cases.getWorkflow({ caseId: id });
    return NextResponse.json(
      { started: workflow?.started ?? false, running: workflow?.running ?? false,
        paused_for_approval: workflow?.pausedForApproval ?? false, error: workflow?.error ?? null,
        round: workflow?.round ?? null },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    if (error instanceof ApiError) return NextResponse.json({ detail: "unavailable" }, { status: error.status });
    throw error;
  }
}
