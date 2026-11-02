import type { Metadata } from "next";
import { Suspense } from "react";

import { startRun, uploadImport } from "@/app/actions/data";
import { ToastForm } from "@/components/forms/toast-form";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, unwrap } from "@/lib/api/client";
import { can, getCurrentUser } from "@/lib/dal";
import { formatDateTime, humanize } from "@/lib/format";

export const metadata: Metadata = { title: "Data & runs · FinRecon AI" };

export default function DataPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Data &amp; runs</h1>
        <p className="text-sm text-muted-foreground">Import source files, then reconcile them to open cases.</p>
      </div>
      <Suspense fallback={<Skeleton className="h-96 w-full" />}>
        <DataView />
      </Suspense>
    </div>
  );
}

async function DataView() {
  const user = await getCurrentUser();
  const client = await api();
  const [sources, batches, runs] = await Promise.all([
    client.GET("/sources").then(unwrap), client.GET("/imports").then(unwrap),
    client.GET("/reconciliation/runs").then(unwrap),
  ]);
  const sourceName = new Map(sources.map((s) => [s.id, s.name]));
  const latest = sources.map((s) => batches.find((b) => b.source_id === s.id && b.status !== "failed")).filter(Boolean);
  const importer = can(user, "data:import");

  return (
    <div className="space-y-6">
      {importer && (
        <div className="grid gap-4 lg:grid-cols-3">
          {sources.map((source) => (
            <Card key={source.id}>
              <CardHeader>
                <CardTitle><h2 className="text-base">{source.name}</h2></CardTitle>
                <CardDescription>{humanize(source.source_type)} · {source.ingestion_method.toUpperCase()}</CardDescription>
              </CardHeader>
              <CardContent>
                <ToastForm action={uploadImport} submit="Upload" pendingLabel="Importing…" className="flex gap-2">
                  <input type="hidden" name="source_id" value={source.id} />
                  <Input type="file" name="file" required aria-label={`File for ${source.name}`}
                         accept={source.ingestion_method === "json" ? ".json,application/json" : ".csv,text/csv"} />
                </ToastForm>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle><h2 className="text-base">Import batches</h2></CardTitle>
          <CardDescription>Uploading the same file again is skipped. Rejected rows are listed with their reasons.</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>File</TableHead><TableHead>Source</TableHead><TableHead>Status</TableHead>
                <TableHead className="text-right">Accepted</TableHead><TableHead className="text-right">Rejected</TableHead>
                <TableHead>Imported</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {batches.map((b) => (
                <TableRow key={b.id}>
                  <TableCell className="font-mono text-xs">{b.file_name}</TableCell>
                  <TableCell>{sourceName.get(b.source_id) ?? "—"}</TableCell>
                  <TableCell><Badge variant={b.status === "failed" ? "destructive" : "secondary"}>{humanize(b.status)}</Badge></TableCell>
                  <TableCell className="text-right tabular-nums">{b.accepted_records.toLocaleString()}</TableCell>
                  <TableCell className="text-right tabular-nums">{b.rejected_records.toLocaleString()}</TableCell>
                  <TableCell className="text-muted-foreground">{formatDateTime(b.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle><h2 className="text-base">Reconciliation runs</h2></CardTitle>
          <CardDescription>Running the same batches again returns the existing run.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {can(user, "reconcile:run") && latest.length > 0 && (
            <ToastForm action={startRun} submit="Reconcile selected batches" pendingLabel="Reconciling…"
                       className="flex flex-wrap items-center gap-4">
              {latest.map((b) => b && (
                <label key={b.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" name="batch_id" value={b.id} defaultChecked className="size-4" />
                  {sourceName.get(b.source_id)}: <span className="font-mono text-xs">{b.file_name}</span>
                </label>
              ))}
            </ToastForm>
          )}
          <Table>
            <TableHeader>
              <TableRow><TableHead>Run</TableHead><TableHead>Status</TableHead><TableHead>Engine</TableHead>
                <TableHead className="text-right">Exceptions</TableHead><TableHead>Started</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {runs.map((r) => {
                const exceptions = Object.values((r.summary as { exceptions?: Record<string, number> }).exceptions ?? {})
                  .reduce((a, b) => a + b, 0);
                return (
                  <TableRow key={r.id}>
                    <TableCell className="font-mono text-xs">{r.id.slice(0, 8)}</TableCell>
                    <TableCell>{humanize(r.status)}</TableCell>
                    <TableCell>{r.engine_version}</TableCell>
                    <TableCell className="text-right tabular-nums">{exceptions.toLocaleString()}</TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(r.created_at)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
