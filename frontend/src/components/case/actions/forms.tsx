"use client";

import { useRef, useState } from "react";

import { addNote, assignCase, decide, transitionCase } from "@/app/actions/cases";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { components } from "@/lib/api/schema";
import { humanize } from "@/lib/format";
import { useActionToast } from "./use-action-toast";

type TransitionOption = components["schemas"]["TransitionOption"];
type User = components["schemas"]["UserOut"];

const SELECT = "h-9 w-full rounded-md border bg-background px-2 text-sm shadow-xs focus-visible:ring-2 focus-visible:ring-ring";

export function AssignForm({ caseId, users, assignedTo }: { caseId: string; users: User[]; assignedTo: string | null }) {
  const { formAction, pending } = useActionToast(assignCase);
  const workers = users.filter((u) => u.roles.some((r) => r === "analyst" || r === "manager"));
  return (
    <form action={formAction} className="space-y-2">
      <input type="hidden" name="case_id" value={caseId} />
      <Label htmlFor="assignee_id">Assign to</Label>
      <div className="flex gap-2">
        <select id="assignee_id" name="assignee_id" defaultValue={assignedTo ?? ""} className={SELECT}>
          <option value="" disabled>Choose a colleague</option>
          {workers.map((u) => <option key={u.id} value={u.id}>{u.full_name} ({u.roles.join(", ")})</option>)}
        </select>
        <Button type="submit" variant="secondary" disabled={pending}>Assign</Button>
      </div>
    </form>
  );
}

export function StatusForm({ caseId, transitions }: { caseId: string; transitions: TransitionOption[] }) {
  const [target, setTarget] = useState<string>(transitions[0]?.to_status ?? "");
  const form = useRef<HTMLFormElement>(null);
  const { formAction, pending } = useActionToast(transitionCase, () => form.current?.reset());
  const option = transitions.find((t) => t.to_status === target);
  return (
    <form ref={form} action={formAction} className="space-y-2">
      <input type="hidden" name="case_id" value={caseId} />
      <Label htmlFor="to_status">Move case to</Label>
      <select id="to_status" name="to_status" value={target} onChange={(e) => setTarget(e.target.value)} className={SELECT}>
        {transitions.map((t) => <option key={t.to_status} value={t.to_status}>{humanize(t.to_status)}</option>)}
      </select>
      {option && option.closure_reasons.length > 0 && (
        <>
          <Label htmlFor="closure_reason">Reason for closing</Label>
          <select id="closure_reason" name="closure_reason" required className={SELECT} defaultValue="">
            <option value="" disabled>Choose a reason</option>
            {option.closure_reasons.map((r) => <option key={r} value={r}>{humanize(r)}</option>)}
          </select>
        </>
      )}
      {option?.requires_comment && (
        <>
          <Label htmlFor="status_comment">Comment (required)</Label>
          <Textarea id="status_comment" name="comment" required rows={2} />
        </>
      )}
      <Button type="submit" variant="secondary" disabled={pending || !option}>Update status</Button>
    </form>
  );
}

export function NoteForm({ caseId }: { caseId: string }) {
  const form = useRef<HTMLFormElement>(null);
  const { formAction, pending } = useActionToast(addNote, () => form.current?.reset());
  return (
    <form ref={form} action={formAction} className="space-y-2">
      <input type="hidden" name="case_id" value={caseId} />
      <Label htmlFor="note">Add a note</Label>
      <Textarea id="note" name="text" rows={3} maxLength={5000} placeholder="What you checked, who you contacted…" required />
      <Button type="submit" variant="secondary" disabled={pending}>Add note</Button>
    </form>
  );
}

const DECISIONS = [
  { value: "approve", label: "Approve", hint: "The resolution is carried out as proposed." },
  { value: "request_evidence", label: "Request evidence", hint: "The case waits for the documents you name." },
  { value: "return_for_investigation", label: "Return for investigation", hint: "The agents investigate again with your comment." },
  { value: "reject", label: "Reject", hint: "The recommendation is not accepted." },
] as const;

export function DecisionForm({ requestId }: { requestId: string }) {
  const [decision, setDecision] = useState<string>("approve");
  const { formAction, pending } = useActionToast(decide);
  const needsComment = decision !== "approve";
  return (
    <form action={formAction} className="space-y-3">
      <input type="hidden" name="request_id" value={requestId} />
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Your decision</legend>
        {DECISIONS.map((d) => (
          <label key={d.value} className="flex cursor-pointer items-start gap-2 rounded-md border p-2 text-sm has-checked:border-ring has-checked:bg-muted/50">
            <input type="radio" name="decision" value={d.value} checked={decision === d.value}
                   onChange={() => setDecision(d.value)} className="mt-0.5" />
            <span><span className="font-medium">{d.label}</span><span className="block text-xs text-muted-foreground">{d.hint}</span></span>
          </label>
        ))}
      </fieldset>
      <div className="space-y-1">
        <Label htmlFor="decision_comment">Comment{needsComment ? " (required)" : " (optional)"}</Label>
        <Textarea id="decision_comment" name="comment" rows={3} required={needsComment} maxLength={5000} />
      </div>
      <Button type="submit" disabled={pending}>{pending ? "Recording…" : "Record decision"}</Button>
    </form>
  );
}
