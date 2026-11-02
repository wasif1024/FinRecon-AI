import type { Metadata } from "next";
import { Suspense } from "react";

import { Meter, RankedBars } from "@/components/charts/ranked-bars";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { getCurrentUser } from "@/lib/dal";
import { formatDateTime, formatMoney, humanize } from "@/lib/format";

export const metadata: Metadata = { title: "Overview · FinRecon AI" };

type CaseStatus = components["schemas"]["CaseStatus"];

// Case statuses grouped the way an operations team reads them. The full overview arrives in 5d.
const GROUPS: { label: string; statuses: CaseStatus[] }[] = [
  { label: "New", statuses: ["new"] },
  { label: "In investigation", statuses: ["investigating", "awaiting_evidence"] },
  { label: "Awaiting approval", statuses: ["awaiting_approval"] },
  { label: "Resolved or closed", statuses: ["resolved", "closed"] },
];

export default function OverviewPage() {
  return (
    <div className="space-y-6">
      <Suspense fallback={<Skeleton className="h-8 w-72" />}>
        <Greeting />
      </Suspense>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {GROUPS.map((group) => (
          <Suspense key={group.label} fallback={<StatSkeleton label={group.label} />}>
            <CaseCount label={group.label} statuses={group.statuses} />
          </Suspense>
        ))}
      </div>
      <Suspense fallback={<Skeleton className="h-80 w-full" />}>
        <LatestRun />
      </Suspense>
    </div>
  );
}

type RunSummary = {
  transactions?: Record<string, number>;
  matches?: Record<string, Record<string, number>>;
  exceptions?: Record<string, number>;
  discrepancy_totals?: Record<string, string>;
};

async function LatestRun() {
  await getCurrentUser();
  const [run] = unwrap(await (await api()).GET("/reconciliation/runs"));
  if (!run) {
    return <p className="rounded-lg border p-8 text-center text-sm text-muted-foreground">No reconciliation has run yet.</p>;
  }
  const summary = run.summary as RunSummary;
  const exceptions = Object.entries(summary.exceptions ?? {}).map(([k, v]) => ({ label: humanize(k), value: v }));
  const records = Object.entries(summary.transactions ?? {}).map(([k, v]) => `${humanize(k)} ${v.toLocaleString()}`);

  return (
    <section aria-labelledby="latest-run" className="space-y-4">
      <div>
        <h2 id="latest-run" className="text-lg font-semibold tracking-tight">Latest reconciliation run</h2>
        <p className="text-sm text-muted-foreground">
          {formatDateTime(run.created_at)} · engine {run.engine_version} · {records.join(" · ")}
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="space-y-4">
          <Card>
            <CardHeader><CardTitle className="text-base">Match rate</CardTitle>
              <CardDescription>Pairs the engine matched on every rule, per leg.</CardDescription></CardHeader>
            <CardContent className="space-y-4">
              {Object.entries(summary.matches ?? {}).map(([leg, counts]) => {
                const total = Object.values(counts).reduce((a, b) => a + b, 0);
                return <Meter key={leg} label={humanize(leg)} value={counts.matched ?? 0} total={total}
                              detail={`${(counts.matched ?? 0).toLocaleString()} of ${total.toLocaleString()} pairs`} />;
              })}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-base">Discrepancies by currency</CardTitle>
              <CardDescription>Never added across currencies.</CardDescription></CardHeader>
            <CardContent className="grid grid-cols-2 gap-3">
              {Object.entries(summary.discrepancy_totals ?? {}).map(([currency, total]) => (
                <div key={currency} className="rounded-md border p-3">
                  <div className="text-xs text-muted-foreground">{currency}</div>
                  <div className="text-lg font-semibold tabular-nums">{formatMoney(total)}</div>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
        <Card>
          <CardHeader><CardTitle className="text-base">Exceptions by category</CardTitle>
            <CardDescription>{exceptions.reduce((a, b) => a + b.value, 0).toLocaleString()} exceptions in this run.</CardDescription></CardHeader>
          <CardContent><RankedBars title="Exceptions" bars={exceptions} /></CardContent>
        </Card>
      </div>
    </section>
  );
}

async function Greeting() {
  const user = await getCurrentUser();
  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">Welcome, {user.full_name}</h1>
      <p className="text-sm text-muted-foreground">Reconciliation cases for your organisation.</p>
    </div>
  );
}

async function CaseCount({ label, statuses }: { label: string; statuses: CaseStatus[] }) {
  await getCurrentUser();
  const totals = await Promise.all(statuses.map(async (status) => {
    const page = unwrap(await (await api()).GET("/cases", { params: { query: { status, limit: 1 } } }));
    return page.total;
  }));
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent className="text-3xl font-semibold tabular-nums">
        {totals.reduce((a, b) => a + b, 0).toLocaleString()}
      </CardContent>
    </Card>
  );
}

function StatSkeleton({ label }: { label: string }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent><Skeleton className="h-9 w-16" /></CardContent>
    </Card>
  );
}
