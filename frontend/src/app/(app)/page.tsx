import type { Metadata } from "next";
import { Suspense } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { getCurrentUser } from "@/lib/dal";

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
    </div>
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
