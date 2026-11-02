import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ResolutionTypeSchema } from "@/gen/finrecon/v1/enums_pb";
import { ApiError } from "@/lib/api/client";
import { getCurrentUser } from "@/lib/dal";
import { enumLabel, formatDateTime, formatMoney } from "@/lib/format";
import { rpc } from "@/lib/rpc";

export const metadata: Metadata = { title: "Approvals · FinRecon AI" };

export default function ApprovalsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Approvals</h1>
        <p className="text-sm text-muted-foreground">
          Recommendations waiting for a manager. Open a case to review its evidence and decide.
        </p>
      </div>
      <Suspense fallback={<div className="space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-28" />)}</div>}>
        <Queue />
      </Suspense>
    </div>
  );
}

async function Queue() {
  const user = await getCurrentUser();
  const client = await rpc();
  let pending;
  try {
    pending = (await client.approvals.listPendingApprovals({})).approvals;
  } catch (error) {
    if (error instanceof ApiError && error.status === 403) {
      return <p className="text-sm text-muted-foreground">Only managers decide recommendations.</p>;
    }
    throw error;
  }
  const { users } = await client.identity.listUsers({});
  const names = new Map(users.map((u) => [u.id, u.fullName]));

  if (pending.length === 0) {
    return <p className="rounded-lg border p-8 text-center text-sm text-muted-foreground">Nothing is waiting for approval.</p>;
  }
  return (
    <ul className="space-y-3">
      {pending.map(({ request, caseNumber, caseTitle, recommendation }) => {
        if (!request || !recommendation) return null;
        const action = (recommendation.proposedAction ?? {}) as { amount?: string | null; currency?: string | null };
        const own = request.requestedByUserId === user.userId;
        return (
          <li key={request.id}>
            <Card>
              <CardHeader>
                <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                  <Link href={`/cases/${request.caseId}`} className="underline-offset-4 hover:underline">
                    <h2>{caseNumber}</h2>
                  </Link>
                  <Badge variant="secondary">{enumLabel(ResolutionTypeSchema, recommendation.resolutionType)}</Badge>
                  <span className="ml-auto text-sm font-normal tabular-nums">{formatMoney(action.amount, action.currency)}</span>
                </CardTitle>
                <CardDescription>{caseTitle}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p>{recommendation.summary}</p>
                <p className="text-xs text-muted-foreground">
                  Requested by {request.requestedByUserId ? names.get(request.requestedByUserId) ?? "a user" : "the workflow"}
                  {" "}· {formatDateTime(request.createdAt)} · v{recommendation.version}
                  {own && " · you started this investigation, so another manager must decide it"}
                </p>
              </CardContent>
            </Card>
          </li>
        );
      })}
    </ul>
  );
}
