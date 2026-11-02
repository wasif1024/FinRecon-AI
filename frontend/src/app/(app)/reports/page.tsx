import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { Suspense } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError, api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { getCurrentUser } from "@/lib/dal";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Reports · FinRecon AI" };

const REPORTS = [
  { kind: "reconciliation-summary", title: "Reconciliation summary", about: "Match rate per leg and exceptions for the latest run." },
  { kind: "outstanding-exceptions", title: "Outstanding exceptions", about: "Open cases by priority, with age and owner." },
  { kind: "unmatched-transactions", title: "Unmatched transactions", about: "Records with no counterpart in the other source." },
  { kind: "duplicates", title: "Duplicate transactions", about: "Confirmed and potential duplicates." },
  { kind: "resolution-statistics", title: "Resolution statistics", about: "Case outcomes, closure reasons, time to resolve." },
] as const;
type Kind = (typeof REPORTS)[number]["kind"];
// The endpoint also serves CSV and PDF, so the generated response type is a union; this page always asks for JSON.
type ReportJson = components["schemas"]["ReportOut"];
const DATE = /^\d{4}-\d{2}-\d{2}$/;

export default function ReportsPage({ searchParams }: PageProps<"/reports">) {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Reports</h1>
        <p className="text-sm text-muted-foreground">
          View in the browser or download as CSV or PDF. Totals are per currency, never combined.
        </p>
      </div>
      <Suspense fallback={<Skeleton className="h-96 w-full" />}>
        <ReportView searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function ReportView({ searchParams }: { searchParams: PageProps<"/reports">["searchParams"] }) {
  const params = await searchParams;
  const kind: Kind = REPORTS.find((r) => r.kind === params.kind)?.kind ?? "reconciliation-summary";
  const from = typeof params.from === "string" && DATE.test(params.from) ? params.from : undefined;
  const to = typeof params.to === "string" && DATE.test(params.to) ? params.to : undefined;
  await getCurrentUser();

  const period = new URLSearchParams({ ...(from ? { from } : {}), ...(to ? { to } : {}) });
  const link = (k: Kind) => `/reports?${new URLSearchParams({ kind: k, ...Object.fromEntries(period) })}`;
  const download = (format: "csv" | "pdf") => `/api/reports/${kind}?${new URLSearchParams({ format, ...Object.fromEntries(period) })}`;

  let report: ReportJson | undefined;
  try {
    report = unwrap(await (await api()).GET("/reports/{kind}", {
      params: { path: { kind }, query: { from, to } },
    })) as ReportJson;
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) throw error;
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
      <nav aria-label="Reports" className="flex flex-col gap-1">
        {REPORTS.map((r) => (
          <Link key={r.kind} href={link(r.kind)} aria-current={r.kind === kind ? "page" : undefined}
                className={cn("rounded-md px-3 py-2 text-sm", r.kind === kind ? "bg-accent font-medium" : "hover:bg-accent/60")}>
            {r.title}
            <span className="block text-xs font-normal text-muted-foreground">{r.about}</span>
          </Link>
        ))}
      </nav>

      <Card>
        <CardHeader>
          <CardTitle><h2 className="text-base">{report?.title ?? "No data yet"}</h2></CardTitle>
          <CardDescription>{report ? `Period: ${report.period}` : "Run a reconciliation first."}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-end gap-3">
            <Form action="/reports" className="flex flex-wrap items-end gap-3">
              <input type="hidden" name="kind" value={kind} />
              <div className="grid gap-1"><Label htmlFor="from">From</Label><Input id="from" type="date" name="from" defaultValue={from} /></div>
              <div className="grid gap-1"><Label htmlFor="to">To</Label><Input id="to" type="date" name="to" defaultValue={to} /></div>
              <Button type="submit" variant="secondary">Apply period</Button>
            </Form>
            {report && (
              <div className="ml-auto flex gap-2">
                <a href={download("csv")} className={buttonVariants({ variant: "outline", size: "sm" })} download>Download CSV</a>
                <a href={download("pdf")} className={buttonVariants({ variant: "outline", size: "sm" })} download>Download PDF</a>
              </div>
            )}
          </div>

          {report && (
            <>
              <div className="max-h-[32rem] overflow-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>{report.columns.map((c) => <TableHead key={c}>{c}</TableHead>)}</TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.rows.length === 0 && (
                      <TableRow><TableCell colSpan={report.columns.length} className="py-8 text-center text-muted-foreground">No rows for this period.</TableCell></TableRow>
                    )}
                    {report.rows.map((row, i) => (
                      <TableRow key={i}>{row.map((cell, j) => <TableCell key={j} className="tabular-nums">{cell ?? ""}</TableCell>)}</TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              {Object.keys(report.totals).length > 0 && (
                <dl className="flex flex-wrap gap-4 text-sm">
                  {Object.entries(report.totals).map(([currency, total]) => (
                    <div key={currency}><dt className="text-xs text-muted-foreground">Total {currency}</dt>
                      <dd className="font-semibold tabular-nums">{total}</dd></div>
                  ))}
                </dl>
              )}
              <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                {report.notes.map((n) => <li key={n}>{n}</li>)}
              </ul>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
