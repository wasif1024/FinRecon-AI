import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApprovalDecisionTypeSchema, ApprovalRequestStatusSchema } from "@/gen/finrecon/v1/enums_pb";
import { enumLabel, formatDateTime, humanize } from "@/lib/format";
import type { Timestamp } from "@bufbuild/protobuf/wkt";

import type { CaseDetail, Names } from "./types";

/** One audit event as shown: the case trail's RPC messages fit as they are; the Audit page maps its REST rows. */
export type AuditRow = {
  id: string | number; occurredAt?: Timestamp | string; eventType: string; actorType: string; actorUserId?: string;
  payload?: unknown;
};

export function ApprovalHistory({ detail, names }: { detail: CaseDetail; names: Names }) {
  if (detail.approvalRequests.length === 0) {
    return <p className="text-sm text-muted-foreground">No approval has been requested for this case.</p>;
  }
  const decisions = new Map(detail.decisions.map((d) => [d.approvalRequestId, d]));
  const versions = new Map(detail.recommendations.map((r) => [r.id, r.version]));
  return (
    <ol className="space-y-3">
      {detail.approvalRequests.map((request) => {
        const decision = decisions.get(request.id);
        return (
          <li key={request.id} className="rounded-md border p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">Recommendation v{versions.get(request.recommendationId) ?? "?"}</span>
              <span className="text-muted-foreground">
                requested by {request.requestedByUserId ? names.get(request.requestedByUserId) ?? "a user" : "the workflow"}
                {" "}· {formatDateTime(request.createdAt)}
              </span>
              <Badge variant="outline" className="ml-auto">{enumLabel(ApprovalRequestStatusSchema, request.status)}</Badge>
            </div>
            {decision && (
              <div className="mt-2 border-t pt-2">
                <span className="font-medium">{enumLabel(ApprovalDecisionTypeSchema, decision.decision)}</span>
                <span className="text-muted-foreground"> by {names.get(decision.reviewerId) ?? "a reviewer"} ·
                  {" "}{formatDateTime(decision.decidedAt)}</span>
                {decision.comments && <p className="mt-1 text-muted-foreground">“{decision.comments}”</p>}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

export function AuditTrail({ events, names }: { events: AuditRow[]; names: Names }) {
  if (events.length === 0) return <p className="text-sm text-muted-foreground">No audit events for this case.</p>;
  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>When</TableHead>
            <TableHead>Event</TableHead>
            <TableHead>By</TableHead>
            <TableHead>Details</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {events.map((e) => (
            <TableRow key={e.id}>
              <TableCell className="whitespace-nowrap text-muted-foreground">{formatDateTime(e.occurredAt)}</TableCell>
              <TableCell className="font-mono text-xs">{e.eventType}</TableCell>
              <TableCell>{e.actorUserId ? names.get(e.actorUserId) ?? "User" : humanize(e.actorType)}</TableCell>
              <TableCell>
                <details>
                  <summary className="cursor-pointer text-xs text-muted-foreground">payload · #{e.id}</summary>
                  <pre className="mt-1 max-w-xl overflow-x-auto rounded bg-muted p-2 text-xs">{JSON.stringify(e.payload ?? {}, null, 2)}</pre>
                </details>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
