import { ShieldAlert } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import type { CaseDetail, User, Workflow } from "../types";
import { AssignForm, DecisionForm, NoteForm, StatusForm } from "./forms";
import { InvestigatePanel } from "./investigate";

/** Only the actions the API says this user may take now; the API enforces them again on submit. */
export function CaseActions({ detail, workflow, users }: { detail: CaseDetail; workflow: Workflow; users: User[] }) {
  const { actions } = detail;
  const sections = [
    actions.pending_approval_request_id && (actions.can_decide || actions.decide_blocked_reason) && (
      actions.can_decide
        ? <DecisionForm key="decide" requestId={actions.pending_approval_request_id} />
        : (
          <Alert key="blocked">
            <ShieldAlert className="size-4" />
            <AlertDescription>{actions.decide_blocked_reason}</AlertDescription>
          </Alert>
        )
    ),
    (actions.can_investigate || workflow.running) && (
      <InvestigatePanel key="investigate" caseId={detail.id} canStart={actions.can_investigate}
                        initiallyRunning={workflow.running} />
    ),
    actions.can_assign && <AssignForm key="assign" caseId={detail.id} users={users} assignedTo={detail.assigned_to} />,
    actions.transitions.length > 0 && <StatusForm key="status" caseId={detail.id} transitions={actions.transitions} />,
    actions.can_add_note && <NoteForm key="note" caseId={detail.id} />,
  ].filter(Boolean);

  if (sections.length === 0) {
    return <p className="text-sm text-muted-foreground">There is nothing you can change on this case right now.</p>;
  }
  return (
    <div className="space-y-4">
      {sections.map((section, i) => (
        <div key={i} className="space-y-4">
          {i > 0 && <Separator />}
          {section}
        </div>
      ))}
    </div>
  );
}
