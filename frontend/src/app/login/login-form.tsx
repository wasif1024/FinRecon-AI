"use client";

import { useActionState } from "react";

import { login, type LoginState } from "@/app/actions/auth";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function LoginForm({ next, expired }: { next?: string; expired: boolean }) {
  const [state, action, pending] = useActionState<LoginState, FormData>(login, { tenant: "demo" });

  return (
    <form action={action} className="space-y-4">
      {expired && !state.error && (
        <Alert>
          <AlertDescription>Your session ended. Sign in again to continue.</AlertDescription>
        </Alert>
      )}
      {state.error && (
        <Alert variant="destructive" aria-live="polite">
          <AlertDescription>{state.error}</AlertDescription>
        </Alert>
      )}
      <input type="hidden" name="next" value={next ?? "/"} />
      <div className="space-y-2">
        <Label htmlFor="tenant">Organisation</Label>
        <Input id="tenant" name="tenant" defaultValue={state.tenant} autoComplete="organization" required />
      </div>
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input id="email" name="email" type="email" defaultValue={state.email} autoComplete="username" required />
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Password</Label>
        <Input id="password" name="password" type="password" autoComplete="current-password" required />
      </div>
      <Button type="submit" className="w-full" disabled={pending}>
        {pending ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}
