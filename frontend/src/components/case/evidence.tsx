import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { humanize, shortId } from "@/lib/format";
import { evidenceLabels } from "./labels";
import type { Evidence, Names, Transaction } from "./types";

export function EvidenceList({ evidence, transactions, names }: {
  evidence: Evidence[]; transactions: Transaction[]; names: Names;
}) {
  if (evidence.length === 0) return <p className="text-sm text-muted-foreground">No evidence recorded yet.</p>;
  const sourceIds = new Map(transactions.map((t) => [t.id, t.source_transaction_id ?? shortId(t.id)]));
  const labels = evidenceLabels(evidence);

  return (
    <ol className="space-y-2">
      {evidence.map((e) => (
        <li key={e.id} id={`evidence-${e.id}`} className="scroll-mt-20 rounded-md border p-3 text-sm target:ring-2 target:ring-ring">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-muted-foreground">{labels.get(e.id)}</span>
            <Badge variant="secondary">{humanize(e.evidence_type)}</Badge>
            <Reference evidence={e} sourceIds={sourceIds} />
            <span className="ml-auto text-xs text-muted-foreground">
              {e.collected_by_user_id ? names.get(e.collected_by_user_id) ?? "Analyst" : "Agent"}
            </span>
          </div>
          {e.excerpt && <p className="mt-1.5 leading-relaxed text-muted-foreground">{e.excerpt}</p>}
        </li>
      ))}
    </ol>
  );
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function Reference({ evidence, sourceIds }: { evidence: Evidence; sourceIds: Map<string, string> }) {
  const ref = evidence.source_reference as Record<string, unknown>;
  if (evidence.transaction_id) {
    const own = sourceIds.get(evidence.transaction_id);
    if (own) return <span className="font-mono text-xs">{own}</span>;
    // A record outside this case (a counterpart, or one the agent found by search).
    const cited = String(ref.cited_as ?? "");
    const label = cited && !UUID.test(cited) ? cited : shortId(evidence.transaction_id);
    return <span className="font-mono text-xs" title={evidence.transaction_id}>{label} <span className="font-sans text-muted-foreground">(related record)</span></span>;
  }
  if (evidence.related_case_id) {
    return (
      <Link href={`/cases/${evidence.related_case_id}`} className="text-xs underline underline-offset-4">
        {String(ref.cited_as ?? "Related case")}
      </Link>
    );
  }
  if (evidence.policy_document_id) {
    const page = typeof ref.page === "number" ? ` (p. ${ref.page})` : "";
    const document = String(ref.document ?? "Policy");
    // The section path starts with the document title; don't repeat it.
    const section = String(ref.section ?? "").replace(new RegExp(`^${escapeRegExp(document)}( › )?`), "");
    return (
      <span className="text-xs">
        {document}{ref.version ? ` v${ref.version}` : ""}{section ? ` › ${section}` : ""}{page}
      </span>
    );
  }
  return null;
}
