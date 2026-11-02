"use client";

import { useRef, type ReactNode } from "react";

import type { ActionResult } from "@/app/actions/cases";
import { useActionToast } from "@/components/case/actions/use-action-toast";
import { Button } from "@/components/ui/button";

/** A form bound to a Server Action: toast for the outcome, reset after success. */
export function ToastForm({ action, children, submit, pendingLabel = "Working…", className }: {
  action: (previous: ActionResult, form: FormData) => Promise<ActionResult>;
  children: ReactNode;
  submit: string;
  pendingLabel?: string;
  className?: string;
}) {
  const form = useRef<HTMLFormElement>(null);
  const { formAction, pending } = useActionToast(action, () => form.current?.reset());
  return (
    <form ref={form} action={formAction} className={className}>
      {children}
      <Button type="submit" variant="secondary" disabled={pending}>{pending ? pendingLabel : submit}</Button>
    </form>
  );
}
