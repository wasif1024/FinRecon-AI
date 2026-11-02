import { NextResponse, type NextRequest } from "next/server";

import { apiDownload } from "@/lib/api/client";

const KINDS = new Set(["reconciliation-summary", "outstanding-exceptions", "unmatched-transactions", "duplicates",
  "resolution-statistics"]);
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Streams a CSV or PDF report from the API; the token stays on the server. Only known parameters are forwarded. */
export async function GET(request: NextRequest, { params }: RouteContext<"/api/reports/[kind]">) {
  const { kind } = await params;
  const format = request.nextUrl.searchParams.get("format");
  if (!KINDS.has(kind) || (format !== "csv" && format !== "pdf")) {
    return NextResponse.json({ detail: "not found" }, { status: 404 });
  }
  const query = new URLSearchParams({ format });
  for (const key of ["from", "to"]) {
    const value = request.nextUrl.searchParams.get(key);
    if (value && DATE.test(value)) query.set(key, value);
  }
  const upstream = await apiDownload(`/reports/${kind}?${query}`);
  if (!upstream.ok) return NextResponse.json({ detail: "report unavailable" }, { status: upstream.status });
  return new Response(upstream.body, {
    headers: {
      "Content-Type": upstream.headers.get("content-type") ?? "application/octet-stream",
      "Content-Disposition": upstream.headers.get("content-disposition") ?? "attachment",
      "Cache-Control": "no-store",
    },
  });
}
