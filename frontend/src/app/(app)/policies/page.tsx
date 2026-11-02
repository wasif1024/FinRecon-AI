import type { Metadata } from "next";
import Form from "next/form";
import { Suspense } from "react";

import { uploadPolicy } from "@/app/actions/data";
import { ToastForm } from "@/components/forms/toast-form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, unwrap } from "@/lib/api/client";
import { can, getCurrentUser } from "@/lib/dal";
import { formatDateTime, humanize } from "@/lib/format";

export const metadata: Metadata = { title: "Policies · FinRecon AI" };

export default function PoliciesPage({ searchParams }: PageProps<"/policies">) {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Policies</h1>
        <p className="text-sm text-muted-foreground">
          The procedures agents search and cite. Only the current version of each document is searched.
        </p>
      </div>
      <Suspense fallback={<Skeleton className="h-96 w-full" />}>
        <PolicyView searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function PolicyView({ searchParams }: { searchParams: PageProps<"/policies">["searchParams"] }) {
  const params = await searchParams;
  const query = typeof params.q === "string" ? params.q.trim().slice(0, 500) : "";
  const user = await getCurrentUser();
  const client = await api();
  const [documents, hits] = await Promise.all([
    client.GET("/policies").then(unwrap),
    query.length >= 2 ? client.GET("/policies/search", { params: { query: { q: query, limit: 6 } } }).then(unwrap)
                      : Promise.resolve(null),
  ]);

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <Card>
        <CardHeader>
          <CardTitle><h2 className="text-base">Search policies</h2></CardTitle>
          <CardDescription>By meaning, the way the agents search: describe the situation.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Form action="/policies" className="flex gap-2">
            <Input name="q" defaultValue={query} placeholder="e.g. bank credit arrived a week late" aria-label="Search policies" />
            <Button type="submit" variant="secondary">Search</Button>
          </Form>
          {hits?.length === 0 && <p className="text-sm text-muted-foreground">No passages found.</p>}
          <ol className="space-y-3">
            {hits?.map((hit) => (
              <li key={hit.chunk_id} className="rounded-md border p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-medium">{hit.title} v{hit.version}</span>
                  <span className="text-muted-foreground">› {hit.section.replace(`${hit.title} › `, "")}{hit.page ? ` (p. ${hit.page})` : ""}</span>
                  <span className="ml-auto tabular-nums text-muted-foreground">relevance {hit.score.toFixed(2)}</span>
                </div>
                <p className="mt-1.5 whitespace-pre-line leading-relaxed">{hit.text}</p>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

      <div className="space-y-6">
        {can(user, "policy:manage") && (
          <Card>
            <CardHeader>
              <CardTitle><h2 className="text-base">Upload a policy</h2></CardTitle>
              <CardDescription>PDF, DOCX, TXT or Markdown. Same title as an existing document creates a new version.</CardDescription>
            </CardHeader>
            <CardContent>
              <ToastForm action={uploadPolicy} submit="Upload and index" pendingLabel="Indexing…" className="space-y-3">
                <div className="space-y-1">
                  <Label htmlFor="policy-file">File</Label>
                  <Input id="policy-file" type="file" name="file" required accept=".pdf,.docx,.txt,.md,.markdown" />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="policy-title">Title (optional)</Label>
                  <Input id="policy-title" name="title" maxLength={300} placeholder="Taken from the document if empty" />
                </div>
              </ToastForm>
            </CardContent>
          </Card>
        )}
        <Card>
          <CardHeader><CardTitle><h2 className="text-base">Documents</h2></CardTitle></CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow><TableHead>Title</TableHead><TableHead>Version</TableHead><TableHead>Status</TableHead>
                  <TableHead className="text-right">Passages</TableHead><TableHead>Added</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {documents.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell className="font-medium">{d.title}</TableCell>
                    <TableCell>v{d.version}</TableCell>
                    <TableCell><Badge variant={d.status === "failed" ? "destructive" : "secondary"}>{humanize(d.status)}</Badge></TableCell>
                    <TableCell className="text-right tabular-nums">{d.chunk_count}</TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(d.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
