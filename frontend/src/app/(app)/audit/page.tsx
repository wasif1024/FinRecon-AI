import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { Suspense } from "react";

import { AuditTrail } from "@/components/case/history";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api, unwrap } from "@/lib/api/client";
import { rpc } from "@/lib/rpc";
import { getCurrentUser } from "@/lib/dal";
import { VerifyChain } from "./verify-chain";

export const metadata: Metadata = { title: "Audit trail · FinRecon AI" };

const PAGE = 100;
const EVENT_TYPE = /^[a-z_.]{1,100}$/;

export default function AuditPage({ searchParams }: PageProps<"/audit">) {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Audit trail</h1>
        <p className="text-sm text-muted-foreground">
          Every import, match, agent step, decision and execution. Append-only and hash-chained.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle><h2 className="text-base">Integrity</h2></CardTitle>
          <CardDescription>Recomputes every hash in your organisation&apos;s chain; any edit or deletion breaks it.</CardDescription>
        </CardHeader>
        <CardContent><VerifyChain /></CardContent>
      </Card>
      <Suspense fallback={<Skeleton className="h-96 w-full" />}>
        <Events searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function Events({ searchParams }: { searchParams: PageProps<"/audit">["searchParams"] }) {
  const params = await searchParams;
  const type = typeof params.type === "string" && EVENT_TYPE.test(params.type) ? params.type : undefined;
  const before = typeof params.before === "string" && /^\d+$/.test(params.before) ? Number(params.before) : undefined;
  await getCurrentUser();
  const client = await api();
  let events;
  try {
    events = unwrap(await client.GET("/audit-events", { params: { query: { event_type: type, before_id: before, limit: PAGE } } }));
  } catch (error) {
    if (error instanceof ApiError && error.status === 403) {
      return <p className="text-sm text-muted-foreground">Only managers and auditors can read the audit trail.</p>;
    }
    throw error;
  }
  const { users } = await (await rpc()).identity.listUsers({});
  const names = new Map(users.map((u) => [u.id, u.fullName]));
  const older = events.length === PAGE ? events[events.length - 1].id : undefined;

  return (
    <Card>
      <CardHeader>
        <CardTitle><h2 className="text-base">Events</h2></CardTitle>
        <CardDescription>Newest first{type ? `, type ${type}` : ""}.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Form action="/audit" className="flex gap-2">
          <Input name="type" defaultValue={type} placeholder="Event type, e.g. approval.decided" aria-label="Event type"
                 className="max-w-xs" />
          <Button type="submit" variant="secondary">Filter</Button>
          {type && <Link href="/audit" className={buttonVariants({ variant: "ghost" })}>Clear</Link>}
        </Form>
        <AuditTrail names={names} events={events.map((e) => ({
          id: e.id, occurredAt: e.occurred_at, eventType: e.event_type, actorType: e.actor_type,
          actorUserId: e.actor_user_id ?? undefined, payload: e.payload,
        }))} />
        {older && (
          <Link href={`/audit?${new URLSearchParams({ ...(type ? { type } : {}), before: String(older) })}`}
                className={buttonVariants({ variant: "outline", size: "sm" })}>Older events</Link>
        )}
      </CardContent>
    </Card>
  );
}
