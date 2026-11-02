import type { Metadata } from "next";
import Form from "next/form";
import { Suspense } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError, api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { getCurrentUser } from "@/lib/dal";
import { humanize } from "@/lib/format";

export const metadata: Metadata = { title: "Agent performance · FinRecon AI" };

type Metrics = components["schemas"]["AgentMetricsOut"];
const PERIODS = [7, 30, 90] as const;

export default function AgentsPage({ searchParams }: PageProps<"/agents">) {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Agent performance</h1>
        <p className="text-sm text-muted-foreground">
          Per agent and prompt version: reliability, speed, cost, grounding and what reviewers decided.
          Full traces are in LangSmith.
        </p>
      </div>
      <Suspense fallback={<Skeleton className="h-96 w-full" />}>
        <MetricsView searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function MetricsView({ searchParams }: { searchParams: PageProps<"/agents">["searchParams"] }) {
  const params = await searchParams;
  const days = PERIODS.find((d) => String(d) === params.days) ?? 30;
  await getCurrentUser();
  let metrics: Metrics[];
  try {
    metrics = unwrap(await (await api()).GET("/metrics/agents", { params: { query: { days } } }));
  } catch (error) {
    if (error instanceof ApiError && error.status === 403) {
      return <p className="text-sm text-muted-foreground">Agent metrics are available to managers, admins and auditors.</p>;
    }
    throw error;
  }
  const llm = metrics.filter((m) => m.model);
  const runs = llm.reduce((a, m) => a + m.runs, 0);
  const completed = llm.reduce((a, m) => a + m.completed, 0);
  const cost = llm.reduce((a, m) => a + Number(m.est_cost_usd), 0);
  const rejectedRuns = llm.reduce((a, m) => a + m.runs_with_rejected_output, 0);

  return (
    <div className="space-y-6">
      <Form action="/agents" className="flex items-center gap-2 text-sm">
        <label htmlFor="days" className="text-muted-foreground">Period</label>
        <select id="days" name="days" defaultValue={String(days)} className="h-9 rounded-md border bg-background px-2">
          {PERIODS.map((d) => <option key={d} value={d}>Last {d} days</option>)}
        </select>
        <Button type="submit" variant="secondary">Apply</Button>
      </Form>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="AI agent runs" value={runs.toLocaleString()} />
        <Stat label="Success rate" value={runs ? `${((completed / runs) * 100).toFixed(1)}%` : "—"} />
        <Stat label="Estimated cost" value={`$${cost.toFixed(2)}`} hint="List price, upper bound" />
        <Stat label="Runs with rejected output" value={runs ? `${rejectedRuns} (${((rejectedRuns / runs) * 100).toFixed(0)}%)` : "—"}
              hint="Evidence that failed verification" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle><h2 className="text-base">By agent and prompt version</h2></CardTitle>
          <CardDescription>Compare versions before releasing a prompt. Deterministic agents show no model or cost.</CardDescription>
        </CardHeader>
        <CardContent>
          {metrics.length === 0 ? <p className="text-sm text-muted-foreground">No agent runs in this period.</p> : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Agent</TableHead><TableHead>Prompt</TableHead>
                    <TableHead className="text-right">Runs</TableHead><TableHead className="text-right">Success</TableHead>
                    <TableHead className="text-right">p50 / p95</TableHead><TableHead className="text-right">Tokens in / out</TableHead>
                    <TableHead className="text-right">Cost / run</TableHead><TableHead className="text-right">Tool errors</TableHead>
                    <TableHead className="text-right">Rejected output</TableHead><TableHead>Reviewer outcomes</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {metrics.map((m) => (
                    <TableRow key={`${m.agent}-${m.prompt}`}>
                      <TableCell className="font-medium">{humanize(m.agent)}</TableCell>
                      <TableCell className="font-mono text-xs" title={m.prompt}>{m.prompt.length > 34 ? `${m.prompt.slice(0, 34)}…` : m.prompt}</TableCell>
                      <TableCell className="text-right tabular-nums">{m.runs}</TableCell>
                      <TableCell className="text-right tabular-nums">{m.success_rate === null ? "—" : `${(m.success_rate * 100).toFixed(0)}%`}</TableCell>
                      <TableCell className="text-right tabular-nums">{seconds(m.latency_p50_s)} / {seconds(m.latency_p95_s)}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {m.model ? `${(m.avg_input_tokens ?? 0).toLocaleString()} / ${(m.avg_output_tokens ?? 0).toLocaleString()}` : "—"}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{m.model ? `$${m.est_cost_per_run_usd}` : "—"}</TableCell>
                      <TableCell className="text-right tabular-nums">{m.tool_calls ? `${m.tool_errors} of ${m.tool_calls}` : "—"}</TableCell>
                      <TableCell className="text-right tabular-nums">{m.model ? `${m.runs_with_rejected_output} runs` : "—"}</TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {Object.entries(m.outcomes).map(([status, n]) => <Badge key={status} variant="secondary">{humanize(status)} {n}</Badge>)}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function seconds(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(1)}s`;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardHeader className="pb-2"><CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle></CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </CardContent>
    </Card>
  );
}
