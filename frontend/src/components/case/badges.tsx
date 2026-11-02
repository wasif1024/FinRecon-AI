import { Badge } from "@/components/ui/badge";
import { humanize, type CaseStatus, type Severity } from "@/lib/format";
import { cn } from "@/lib/utils";

const STATUS_STYLE: Record<CaseStatus, string> = {
  new: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200",
  investigating: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-200",
  awaiting_evidence: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  awaiting_approval: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-200",
  approved: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  rejected: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-200",
  resolved: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  closed: "bg-muted text-muted-foreground",
};

const PRIORITY_STYLE: Record<Severity, string> = {
  low: "border-border text-muted-foreground",
  medium: "border-amber-300 text-amber-800 dark:border-amber-800 dark:text-amber-200",
  high: "border-orange-400 text-orange-800 dark:border-orange-700 dark:text-orange-200",
  critical: "border-rose-500 bg-rose-50 text-rose-800 dark:border-rose-700 dark:bg-rose-950 dark:text-rose-200",
};

export function StatusBadge({ status }: { status: CaseStatus }) {
  return <Badge className={cn("border-transparent font-medium", STATUS_STYLE[status])}>{humanize(status)}</Badge>;
}

export function PriorityBadge({ priority }: { priority: Severity }) {
  return <Badge variant="outline" className={cn("capitalize", PRIORITY_STYLE[priority])}>{priority}</Badge>;
}
