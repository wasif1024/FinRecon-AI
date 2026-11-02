import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime, humanize } from "@/lib/format";
import type { AuditEvent, CaseDetail, Names } from "./types";

export function ApprovalHistory({ detail, names }: { detail: CaseDetail; names: Names }) {
  if (detail.approval_requests.length === 0) {
    return <p className="text-sm text-muted-foreground">No approval has been requested for this case.</p>;
  }
  const decisions = new Map(detail.decisions.map((d) => [d.approval_request_id, d]));
  const versions = new Map(detail.recommendations.map((r) => [r.id, r.version]));
  return (
    <ol className="space-y-3">
      {detail.approval_requests.map((request) => {
        const decision = decisions.get(request.id);
        return (
          <li key={request.id} className="rounded-md border p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">Recommendation v{versions.get(request.recommendation_id) ?? "?"}</span>
              <span className="text-muted-foreground">
                requested by {request.requested_by_user_id ? names.get(request.requested_by_user_id) ?? "a user" : "the workflow"}
                {" "}· {formatDateTime(request.created_at)}
              </span>
              <Badge variant="outline" className="ml-auto">{humanize(request.status)}</Badge>
            </div>
            {decision && (
              <div className="mt-2 border-t pt-2">
                <span className="font-medium">{humanize(decision.decision)}</span>
                <span className="text-muted-foreground"> by {names.get(decision.reviewer_id) ?? "a reviewer"} ·
                  {" "}{formatDateTime(decision.decided_at)}</span>
                {decision.comments && <p className="mt-1 text-muted-foreground">“{decision.comments}”</p>}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

export function AuditTrail({ events, names }: { events: AuditEvent[]; names: Names }) {
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
              <TableCell className="whitespace-nowrap text-muted-foreground">{formatDateTime(e.occurred_at)}</TableCell>
              <TableCell className="font-mono text-xs">{e.event_type}</TableCell>
              <TableCell>{e.actor_user_id ? names.get(e.actor_user_id) ?? "User" : humanize(e.actor_type)}</TableCell>
              <TableCell>
                <details>
                  <summary className="cursor-pointer text-xs text-muted-foreground">payload · #{e.id}</summary>
                  <pre className="mt-1 max-w-xl overflow-x-auto rounded bg-muted p-2 text-xs">{JSON.stringify(e.payload, null, 2)}</pre>
                </details>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
