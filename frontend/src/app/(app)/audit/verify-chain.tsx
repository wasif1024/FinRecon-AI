"use client";

import { ShieldAlert, ShieldCheck } from "lucide-react";
import { useState, useTransition } from "react";

import { verifyChain, type ChainResult } from "@/app/actions/data";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export function VerifyChain() {
  const [result, setResult] = useState<ChainResult | null>(null);
  const [pending, start] = useTransition();
  return (
    <div className="space-y-3">
      <Button variant="secondary" disabled={pending} onClick={() => start(async () => setResult(await verifyChain()))}>
        {pending ? "Verifying…" : "Verify chain"}
      </Button>
      {result?.intact === true && (
        <Alert role="status">
          <ShieldCheck className="size-4" />
          <AlertDescription>Intact: all {result.checked?.toLocaleString()} events match their recorded hashes.</AlertDescription>
        </Alert>
      )}
      {result?.intact === false && (
        <Alert variant="destructive" role="alert">
          <ShieldAlert className="size-4" />
          <AlertDescription>
            The chain is broken at event #{result.firstBad}: that record or one before it was altered.
          </AlertDescription>
        </Alert>
      )}
      {result?.error && <p className="text-sm text-destructive">{result.error}</p>}
    </div>
  );
}
