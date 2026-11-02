import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense, type ReactNode } from "react";

import { PriorityBadge, StatusBadge } from "@/components/case/badges";
import { EvidenceList } from "@/components/case/evidence";
import { Findings } from "@/components/case/findings";
import { ApprovalHistory, AuditTrail } from "@/components/case/history";
import { Recommendations } from "@/components/case/recommendations";
import { MatchExplanations, Transactions } from "@/components/case/transactions";
import type { CaseDetail, Names, Workflow } from "@/components/case/types";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api, unwrap } from "@/lib/api/client";
import { can, getCurrentUser } from "@/lib/dal";
import { formatDateTime, formatMoney, humanize } from "@/lib/format";

export const metadata: Metadata = { title: "Case · FinRecon AI" };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default function CasePage({ params }: PageProps<"/cases/[id]">) {
  return (
    <div className="space-y-6">
      <Link href="/cases" className="text-sm text-muted-foreground hover:text-foreground">← All cases</Link>
      <Suspense fallback={<DetailSkeleton />}>
        <CaseView params={params} />
      </Suspense>
    </div>
  );
}

async function CaseView({ params }: { params: PageProps<"/cases/[id]">["params"] }) {
  const { id } = await params;
  if (!UUID.test(id)) notFound();
  const user = await getCurrentUser();
  const client = await api();

  let detail: CaseDetail;
  try {
    detail = unwrap(await client.GET("/cases/{case_id}", { params: { path: { case_id: id } } }));
  } catch (error) {
    // Another tenant's case answers exactly like a missing one.
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }
  const [users, workflow, audit] = await Promise.all([
    client.GET("/users").then(unwrap),
    client.GET("/cases/{case_id}/workflow", { params: { path: { case_id: id } } }).then(unwrap),
    can(user, "audit:read")
      ? client.GET("/cases/{case_id}/audit", { params: { path: { case_id: id } } }).then(unwrap)
      : Promise.resolve(null),
  ]);
  const names: Names = new Map(users.map((u) => [u.id, u.full_name]));

  return (
    <div className="space-y-6">
      <Header detail={detail} names={names} workflow={workflow} />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="space-y-6">
          <Section title="Recommendation" description="What the resolution agent proposes; a manager decides.">
            <Recommendations recommendations={detail.recommendations} />
          </Section>
          <Section title="Findings" description="Facts are backed by evidence; hypotheses are not yet confirmed.">
            <Findings findings={detail.findings} evidence={detail.evidence} />
          </Section>
        </div>
        <div className="space-y-6">
          <Section title="Exception" description={`Rule ${detail.exception.rule_code ?? "—"}`}>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Category</dt><dd>{humanize(detail.exception.category)}</dd>
              <dt className="text-muted-foreground">Severity</dt><dd className="capitalize">{detail.exception.severity}</dd>
            </dl>
            <details className="mt-3 text-sm">
              <summary className="cursor-pointer text-muted-foreground">Engine details</summary>
              <pre className="mt-2 overflow-x-auto rounded bg-muted p-3 text-xs">{JSON.stringify(detail.exception.details, null, 2)}</pre>
            </details>
          </Section>
          <Section title="Evidence" description="Sources the findings and recommendation cite.">
            <EvidenceList evidence={detail.evidence} transactions={detail.transactions} names={names} />
          </Section>
        </div>
      </div>

      <Section title="Transactions and matching" description="The records behind this case and how the engine compared them.">
        <div className="space-y-4">
          <Transactions transactions={detail.transactions} />
          <MatchExplanations matches={detail.matches} />
        </div>
      </Section>

      <Section title="Approval history">
        <ApprovalHistory detail={detail} names={names} />
      </Section>

      {audit && (
        <Section title="Audit trail" description="Append-only and hash-chained; verify the chain on the Audit page.">
          <AuditTrail events={audit} names={names} />
        </Section>
      )}
    </div>
  );
}

function Header({ detail, names, workflow }: { detail: CaseDetail; names: Names; workflow: Workflow }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{detail.case_number}</h1>
        <StatusBadge status={detail.status} />
        <PriorityBadge priority={detail.priority} />
      </div>
      <p className="text-muted-foreground">{detail.title}</p>
      <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Discrepancy" value={formatMoney(detail.discrepancy_amount, detail.currency)} />
        <Fact label="Assigned to" value={detail.assigned_to ? names.get(detail.assigned_to) ?? "Unknown user" : "Unassigned"} />
        <Fact label="Opened" value={formatDateTime(detail.created_at)} />
        <Fact label="Workflow" value={workflowLabel(workflow)} />
      </dl>
      {workflow.error && <p className="text-sm text-rose-700 dark:text-rose-300">Last agent run failed: {workflow.error}</p>}
    </div>
  );
}

function workflowLabel(w: Workflow): string {
  if (!w.started) return "Not started";
  if (w.running) return "Agents working…";
  if (w.paused_for_approval) return `Waiting for approval (round ${w.round})`;
  if (w.error) return "Failed";
  return `Finished (round ${w.round})`;
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}

function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle><h2 className="text-base">{title}</h2></CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function DetailSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-5 w-96" />
      <div className="grid gap-6 xl:grid-cols-2">
        <Skeleton className="h-64" />
        <Skeleton className="h-64" />
      </div>
    </div>
  );
}
