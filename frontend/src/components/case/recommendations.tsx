import { AlertTriangle, CheckCircle2 } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { formatDateTime, formatMoney, humanize } from "@/lib/format";
import type { Recommendation } from "./types";

type Action = { action_steps?: string[]; contact?: string; amount?: string | null; currency?: string | null;
                warnings?: string[]; policy_evidence_ids?: string[] };

export function Recommendations({ recommendations }: { recommendations: Recommendation[] }) {
  if (recommendations.length === 0) {
    return <p className="text-sm text-muted-foreground">No recommendation yet.</p>;
  }
  const [latest, ...older] = [...recommendations].reverse();
  return (
    <div className="space-y-4">
      <RecommendationCard recommendation={latest} />
      {older.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">Earlier versions ({older.length})</summary>
          <div className="mt-3 space-y-3">
            {older.map((r) => <RecommendationCard key={r.id} recommendation={r} compact />)}
          </div>
        </details>
      )}
    </div>
  );
}

function RecommendationCard({ recommendation: r, compact = false }: { recommendation: Recommendation; compact?: boolean }) {
  const action = r.proposed_action as Action;
  const result = r.execution_result as { case_status?: string; follow_up?: { steps?: string[] }; match_id?: string } | null;
  return (
    <div className="space-y-3 rounded-lg border p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{humanize(r.resolution_type)}</span>
        <Badge variant="secondary">v{r.version}</Badge>
        <Badge variant="outline">{humanize(r.status)}</Badge>
        {r.confidence && <span className="text-xs text-muted-foreground">confidence {Number(r.confidence).toFixed(1)}</span>}
        <span className="ml-auto tabular-nums">{formatMoney(action.amount, action.currency)}</span>
      </div>
      <p className="leading-relaxed">{r.summary}</p>
      {!compact && (
        <>
          <p className="leading-relaxed text-muted-foreground">{r.rationale}</p>
          {action.action_steps && action.action_steps.length > 0 && (
            <ol className="list-decimal space-y-1 pl-5">
              {action.action_steps.map((step, i) => <li key={i}>{step}</li>)}
            </ol>
          )}
          <p className="text-xs text-muted-foreground">
            Contact: {humanize(action.contact)} · policy passages cited: {action.policy_evidence_ids?.length ?? 0} ·
            amount from the matching engine, not the model
          </p>
          {action.warnings?.map((w) => (
            <Alert key={w}>
              <AlertTriangle className="size-4" />
              <AlertDescription>{w}</AlertDescription>
            </Alert>
          ))}
          {result && (
            <Alert>
              <CheckCircle2 className="size-4" />
              <AlertDescription>
                Executed {formatDateTime(r.executed_at)}: case moved to {humanize(result.case_status)}
                {result.match_id ? "; manual match recorded" : ""}
                {result.follow_up?.steps?.length ? `; ${result.follow_up.steps.length} follow-up steps recorded` : ""}.
              </AlertDescription>
            </Alert>
          )}
        </>
      )}
    </div>
  );
}
