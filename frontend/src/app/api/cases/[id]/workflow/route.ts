import { NextResponse } from "next/server";

import { api } from "@/lib/api/client";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Workflow status for live progress polling. Proxies the API with the session cookie; the token stays server-side. */
export async function GET(_: Request, { params }: RouteContext<"/api/cases/[id]/workflow">) {
  const { id } = await params;
  if (!UUID.test(id)) return NextResponse.json({ detail: "not found" }, { status: 404 });
  const { data, response } = await (await api()).GET("/cases/{case_id}/workflow", { params: { path: { case_id: id } } });
  if (!response.ok || !data) return NextResponse.json({ detail: "unavailable" }, { status: response.status });
  return NextResponse.json(
    { started: data.started, running: data.running, paused_for_approval: data.paused_for_approval, error: data.error,
      round: data.round },
    { headers: { "Cache-Control": "no-store" } },
  );
}
