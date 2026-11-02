"use client";

import { useActionState, useEffect, useRef } from "react";
import { toast } from "sonner";

import type { ActionResult } from "@/app/actions/cases";

type Action = (previous: ActionResult, form: FormData) => Promise<ActionResult>;

/** useActionState plus one toast per outcome. Reacts only to new results, not to re-renders. */
export function useActionToast(action: Action, onSuccess?: () => void) {
  const [state, formAction, pending] = useActionState<ActionResult, FormData>(action, {});
  const callback = useRef(onSuccess);
  useEffect(() => {
    callback.current = onSuccess;
  });
  useEffect(() => {
    if (state.ok) {
      toast.success(state.ok);
      callback.current?.();
    }
    if (state.error) toast.error(state.error);
  }, [state]);
  return { state, formAction, pending };
}
