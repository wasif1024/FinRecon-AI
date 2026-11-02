import { AlertTriangle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { FindingType } from "@/gen/finrecon/v1/enums_pb";
import { humanize } from "@/lib/format";
import { evidenceLabels } from "./labels";
import type { Evidence, Finding } from "./types";

// Prefix the backend adds when a "verified fact" lost its evidence on verification (agents/persistence.py).
const UNVERIFIED = "[Unverified: cited evidence could not be confirmed] ";

const GROUPS = [
  { type: FindingType.VERIFIED_FACT, title: "Verified facts", hint: "Shown directly by a source record, rule result or policy." },
  { type: FindingType.HYPOTHESIS, title: "Hypotheses", hint: "Explanations that still need confirming." },
  { type: FindingType.OUTSTANDING_REQUIREMENT, title: "Outstanding requirements", hint: "What a person needs to confirm or rule out." },
] as const;

export function Findings({ findings, evidence }: { findings: Finding[]; evidence: Evidence[] }) {
  if (findings.length === 0) {
    return <p className="text-sm text-muted-foreground">No findings yet. Run an investigation to gather them.</p>;
  }
  const labels = evidenceLabels(evidence);
  return (
    <div className="space-y-6">
      {GROUPS.map(({ type, title, hint }) => {
        const items = findings.filter((f) => f.findingType === type);
        if (items.length === 0) return null;
        return (
          <section key={type} aria-labelledby={`findings-${type}`} className="space-y-2">
            <div>
              <h3 id={`findings-${type}`} className="text-sm font-semibold">{title} <span className="text-muted-foreground">({items.length})</span></h3>
              <p className="text-xs text-muted-foreground">{hint}</p>
            </div>
            <ul className="space-y-2">
              {items.map((f) => <FindingItem key={f.id} finding={f} labels={labels} />)}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function FindingItem({ finding, labels }: { finding: Finding; labels: Map<string, string> }) {
  const unverified = finding.statement.startsWith(UNVERIFIED);
  const statement = unverified ? finding.statement.slice(UNVERIFIED.length) : finding.statement;
  return (
    <li className="rounded-md border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        {unverified && (
          <Badge variant="outline" className="border-amber-400 text-amber-800 dark:text-amber-200">
            <AlertTriangle className="size-3" aria-hidden /> Evidence not confirmed
          </Badge>
        )}
        {finding.confidence && <Badge variant="secondary">confidence {Number(finding.confidence).toFixed(1)}</Badge>}
        <span className="ml-auto text-xs text-muted-foreground">by {humanize(finding.recordedBy)}</span>
      </div>
      <p className="mt-1.5 leading-relaxed">{statement}</p>
      {finding.evidenceIds.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {finding.evidenceIds.map((id) => (
            <a key={id} href={`#evidence-${id}`}
               className="rounded border px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted">
              {labels.get(id) ?? "?"}
            </a>
          ))}
        </div>
      )}
    </li>
  );
}
