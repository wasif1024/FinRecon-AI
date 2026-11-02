import { Badge } from "@/components/ui/badge";
import { CaseStatus, CaseStatusSchema, Severity, SeveritySchema } from "@/gen/finrecon/v1/enums_pb";
import { enumLabel, enumValue } from "@/lib/format";
import { cn } from "@/lib/utils";

const STATUS_STYLE: Partial<Record<CaseStatus, string>> = {
  [CaseStatus.NEW]: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200",
  [CaseStatus.INVESTIGATING]: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-200",
  [CaseStatus.AWAITING_EVIDENCE]: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  [CaseStatus.AWAITING_APPROVAL]: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-200",
  [CaseStatus.APPROVED]: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  [CaseStatus.REJECTED]: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-200",
  [CaseStatus.RESOLVED]: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  [CaseStatus.CLOSED]: "bg-muted text-muted-foreground",
};

const PRIORITY_STYLE: Partial<Record<Severity, string>> = {
  [Severity.LOW]: "border-border text-muted-foreground",
  [Severity.MEDIUM]: "border-amber-300 text-amber-800 dark:border-amber-800 dark:text-amber-200",
  [Severity.HIGH]: "border-orange-400 text-orange-800 dark:border-orange-700 dark:text-orange-200",
  [Severity.CRITICAL]: "border-rose-500 bg-rose-50 text-rose-800 dark:border-rose-700 dark:bg-rose-950 dark:text-rose-200",
};

export function StatusBadge({ status }: { status: CaseStatus }) {
  return <Badge className={cn("border-transparent font-medium", STATUS_STYLE[status])}>{enumLabel(CaseStatusSchema, status)}</Badge>;
}

export function PriorityBadge({ priority }: { priority: Severity }) {
  return <Badge variant="outline" className={cn("capitalize", PRIORITY_STYLE[priority])}>{enumValue(SeveritySchema, priority)}</Badge>;
}
