import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { Suspense } from "react";

import { PriorityBadge, StatusBadge } from "@/components/case/badges";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { CaseStatus, CaseStatusSchema, Severity, SeveritySchema } from "@/gen/finrecon/v1/enums_pb";
import { getCurrentUser } from "@/lib/dal";
import { enumFrom, formatDate, formatMoney, humanize } from "@/lib/format";
import { rpc } from "@/lib/rpc";

export const metadata: Metadata = { title: "Cases · FinRecon AI" };

const PAGE_SIZE = 25;
// Filters use the API's own values in the URL (?status=awaiting_approval).
const STATUSES = ["new", "investigating", "awaiting_evidence", "awaiting_approval", "approved", "rejected", "resolved",
  "closed"];
const PRIORITIES = ["critical", "high", "medium", "low"];

type Filters = { status?: string; priority?: string; mine: boolean; page: number };

function parse(params: Record<string, string | string[] | undefined>): Filters {
  const one = (key: string) => (typeof params[key] === "string" ? (params[key] as string) : undefined);
  const status = one("status");
  const priority = one("priority");
  const page = Number.parseInt(one("page") ?? "1", 10);
  return {
    status: status && STATUSES.includes(status) ? status : undefined,
    priority: priority && PRIORITIES.includes(priority) ? priority : undefined,
    mine: one("mine") === "1",
    page: Number.isFinite(page) && page > 0 ? page : 1,
  };
}

export default function CasesPage({ searchParams }: PageProps<"/cases">) {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Cases</h1>
        <p className="text-sm text-muted-foreground">One case per reconciliation exception, newest work first.</p>
      </div>
      <Suspense fallback={<ListSkeleton />}>
        <CaseList searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function CaseList({ searchParams }: { searchParams: PageProps<"/cases">["searchParams"] }) {
  const filters = parse(await searchParams);
  const user = await getCurrentUser();
  const client = await rpc();
  const [page, { users }] = await Promise.all([
    client.cases.listCases({
      status: enumFrom<CaseStatus>(CaseStatusSchema, filters.status),
      priority: enumFrom<Severity>(SeveritySchema, filters.priority),
      limit: PAGE_SIZE, offset: (filters.page - 1) * PAGE_SIZE, assignedTo: filters.mine ? user.userId : undefined,
    }),
    client.identity.listUsers({}),
  ]);
  const names = new Map(users.map((u) => [u.id, u.fullName]));
  const pages = Math.max(1, Math.ceil(page.total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      <Form action="/cases" className="flex flex-wrap items-end gap-3">
        <FilterSelect name="status" label="Status" value={filters.status} options={STATUSES} />
        <FilterSelect name="priority" label="Priority" value={filters.priority} options={PRIORITIES} />
        <label className="flex h-9 items-center gap-2 text-sm">
          <input type="checkbox" name="mine" value="1" defaultChecked={filters.mine} className="size-4" />
          Assigned to me
        </label>
        <Button type="submit" variant="secondary">Apply</Button>
        {(filters.status || filters.priority || filters.mine) && (
          <Link href="/cases" className={buttonVariants({ variant: "ghost" })}>Clear</Link>
        )}
        <span className="ml-auto text-sm text-muted-foreground">{page.total.toLocaleString()} cases</span>
      </Form>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-32">Case</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Priority</TableHead>
              <TableHead className="text-right">Discrepancy</TableHead>
              <TableHead>Assigned</TableHead>
              <TableHead>Opened</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="py-10 text-center text-muted-foreground">
                  No cases match these filters.
                </TableCell>
              </TableRow>
            )}
            {page.items.map((c) => (
              <TableRow key={c.id}>
                <TableCell className="font-medium">
                  <Link href={`/cases/${c.id}`} className="underline-offset-4 hover:underline">{c.caseNumber}</Link>
                </TableCell>
                <TableCell className="max-w-md truncate" title={c.title}>{c.title}</TableCell>
                <TableCell><StatusBadge status={c.status} /></TableCell>
                <TableCell><PriorityBadge priority={c.priority} /></TableCell>
                <TableCell className="text-right tabular-nums">{formatMoney(c.discrepancyAmount, c.currency)}</TableCell>
                <TableCell className="text-muted-foreground">
                  {c.assignedTo ? names.get(c.assignedTo) ?? "Unknown user" : "Unassigned"}
                </TableCell>
                <TableCell className="text-muted-foreground">{formatDate(c.createdAt)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Pagination filters={filters} pages={pages} />
    </div>
  );
}

function FilterSelect({ name, label, value, options }: { name: string; label: string; value?: string;
                                                         options: string[] }) {
  return (
    <label className="grid gap-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <select name={name} defaultValue={value ?? ""}
              className="h-9 rounded-md border bg-background px-2 text-sm shadow-xs focus-visible:ring-2 focus-visible:ring-ring">
        <option value="">Any</option>
        {options.map((o) => <option key={o} value={o}>{humanize(o)}</option>)}
      </select>
    </label>
  );
}

function Pagination({ filters, pages }: { filters: Filters; pages: number }) {
  if (pages <= 1) return null;
  const href = (page: number) => {
    const query = new URLSearchParams();
    if (filters.status) query.set("status", filters.status);
    if (filters.priority) query.set("priority", filters.priority);
    if (filters.mine) query.set("mine", "1");
    query.set("page", String(page));
    return `/cases?${query}`;
  };
  return (
    <nav aria-label="Pagination" className="flex items-center justify-end gap-2 text-sm">
      <span className="text-muted-foreground">Page {filters.page} of {pages}</span>
      <PageLink href={filters.page > 1 ? href(filters.page - 1) : undefined}>Previous</PageLink>
      <PageLink href={filters.page < pages ? href(filters.page + 1) : undefined}>Next</PageLink>
    </nav>
  );
}

function PageLink({ href, children }: { href?: string; children: string }) {
  const style = buttonVariants({ variant: "outline", size: "sm" });
  return href ? <Link href={href} className={style}>{children}</Link>
              : <span className={`${style} pointer-events-none opacity-50`} aria-disabled="true">{children}</span>;
}

function ListSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-9 w-96" />
      {Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-10 w-full" />)}
    </div>
  );
}
