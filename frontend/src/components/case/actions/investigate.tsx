"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { startInvestigation } from "@/app/actions/cases";
import { Button } from "@/components/ui/button";
import { useActionToast } from "./use-action-toast";

type Status = { running: boolean; paused_for_approval: boolean };

const POLL_MS = 3000;
const GIVE_UP_MS = 6 * 60 * 1000;

/**
 * Starts the agent workflow and follows it live. The API answers 202 before its background task starts, so polling
 * continues until the workflow has been seen running and then stops (paused for approval, finished or failed).
 */
export function InvestigatePanel({ caseId, canStart, initiallyRunning }: {
  caseId: string; canStart: boolean; initiallyRunning: boolean;
}) {
  const [watching, setWatching] = useState(initiallyRunning);
  const { formAction, pending } = useActionToast(startInvestigation, () => setWatching(true));
  const stopWatching = useCallback(() => setWatching(false), []);

  return (
    <div className="space-y-2">
      {watching ? <Progress caseId={caseId} onDone={stopWatching} /> : canStart && (
        <form action={formAction}>
          <input type="hidden" name="case_id" value={caseId} />
          <Button type="submit" disabled={pending} className="w-full">
            {pending ? "Starting…" : "Run AI investigation"}
          </Button>
          <p className="mt-1 text-xs text-muted-foreground">
            Agents gather evidence and propose a resolution. Nothing is carried out without a manager&apos;s decision.
          </p>
        </form>
      )}
    </div>
  );
}

function Progress({ caseId, onDone }: { caseId: string; onDone: () => void }) {
  const router = useRouter();
  const [elapsed, setElapsed] = useState(0);
  const [failed, setFailed] = useState(false);
  const done = useRef(onDone);
  useEffect(() => {
    done.current = onDone;
  });

  useEffect(() => {
    const started = Date.now();
    let seenRunning = false;
    let stopped = false;
    const tick = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    const poll = setInterval(async () => {
      const response = await fetch(`/api/cases/${caseId}/workflow`, { cache: "no-store" });
      if (stopped) return;
      if (!response.ok) { setFailed(true); return; }
      const status: Status = await response.json();
      seenRunning ||= status.running;
      // A previous run's error or result must not end this one early: finish only after seeing this run work,
      // or once it has reached the approval checkpoint.
      const finished = !status.running && (seenRunning || status.paused_for_approval);
      if (finished || Date.now() - started > GIVE_UP_MS) {
        stopped = true;
        clearInterval(poll);
        clearInterval(tick);
        router.refresh();
        done.current();
      }
    }, POLL_MS);
    return () => { stopped = true; clearInterval(poll); clearInterval(tick); };
  }, [caseId, router]);

  return (
    <div role="status" aria-live="polite" className="flex items-center gap-2 rounded-md border bg-muted/40 p-3 text-sm">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {failed ? "Lost contact with the server; refresh the page." : `Agents working… ${elapsed}s`}
    </div>
  );
}
