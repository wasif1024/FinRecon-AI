import type { Metadata } from "next";
import { Suspense } from "react";

import { createUser, setActive, setPassword, updateRoles } from "@/app/actions/users";
import { ToastForm } from "@/components/forms/toast-form";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Account } from "@/gen/finrecon/v1/users_pb";
import { can, getCurrentUser } from "@/lib/dal";
import { formatDate, humanize } from "@/lib/format";
import { rpc } from "@/lib/rpc";

export const metadata: Metadata = { title: "Users · FinRecon AI" };

const ROLES = [
  { value: "analyst", hint: "works assigned cases, runs investigations" },
  { value: "manager", hint: "assigns cases, decides recommendations" },
  { value: "admin", hint: "users, policy library" },
  { value: "auditor", hint: "read-only, audit trail" },
] as const;

export default function UsersPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Users</h1>
        <p className="text-sm text-muted-foreground">
          People in your organisation and what they may do. Changes apply from their next request and are audited.
        </p>
      </div>
      <Suspense fallback={<Skeleton className="h-96 w-full" />}>
        <UserAdmin />
      </Suspense>
    </div>
  );
}

async function UserAdmin() {
  const me = await getCurrentUser();
  if (!can(me, "user:manage")) return <p className="text-sm text-muted-foreground">Only admins manage users.</p>;
  const { accounts } = await (await rpc()).users.listAccounts({});

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]">
      <Card>
        <CardHeader>
          <CardTitle><h2 className="text-base">People</h2></CardTitle>
          <CardDescription>
            {accounts.filter((a) => a.active).length} active of {accounts.length}. You cannot deactivate yourself or
            remove your own admin role, and the last admin always stays.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Person</TableHead>
                  <TableHead>Roles</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Password</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {accounts.map((account) => (
                  <AccountRow key={account.id} account={account} self={account.id === me.userId} />
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <Card className="self-start">
        <CardHeader>
          <CardTitle><h2 className="text-base">Add a user</h2></CardTitle>
          <CardDescription>They sign in with this email and password; share the password separately.</CardDescription>
        </CardHeader>
        <CardContent>
          <ToastForm action={createUser} submit="Add user" pendingLabel="Adding…" className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="new_email">Email</Label>
              <Input id="new_email" name="email" type="email" required autoComplete="off" />
            </div>
            <div className="space-y-1">
              <Label htmlFor="new_name">Full name</Label>
              <Input id="new_name" name="full_name" required maxLength={200} autoComplete="off" />
            </div>
            <fieldset className="space-y-1">
              <legend className="text-sm font-medium">Roles</legend>
              {ROLES.map((role) => (
                <label key={role.value} className="flex items-start gap-2 text-sm">
                  <input type="checkbox" name="roles" value={role.value} defaultChecked={role.value === "analyst"}
                         className="mt-0.5 size-4" />
                  <span>{humanize(role.value)} <span className="text-xs text-muted-foreground">{role.hint}</span></span>
                </label>
              ))}
            </fieldset>
            <div className="space-y-1">
              <Label htmlFor="new_password">Initial password</Label>
              <Input id="new_password" name="password" type="password" required minLength={12} autoComplete="new-password" />
            </div>
          </ToastForm>
        </CardContent>
      </Card>
    </div>
  );
}

function AccountRow({ account, self }: { account: Account; self: boolean }) {
  return (
    <TableRow aria-label={account.email} className={account.active ? undefined : "text-muted-foreground"}>
      <TableCell className="align-top">
        <div className="font-medium">{account.fullName}{self && <span className="ml-2 text-xs text-muted-foreground">(you)</span>}</div>
        <div className="text-xs text-muted-foreground">{account.email}</div>
        <div className="text-xs text-muted-foreground">since {formatDate(account.createdAt)}</div>
      </TableCell>
      <TableCell className="align-top">
        <ToastForm action={updateRoles} submit="Save roles" pendingLabel="Saving…" className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <input type="hidden" name="user_id" value={account.id} />
          {ROLES.map((role) => (
            <label key={role.value} className="flex items-center gap-1.5 text-sm">
              <input type="checkbox" name="roles" value={role.value} defaultChecked={account.roles.includes(role.value)}
                     aria-label={`${role.value} for ${account.email}`} className="size-4" />
              {humanize(role.value)}
            </label>
          ))}
        </ToastForm>
      </TableCell>
      <TableCell className="align-top">
        <div className="flex flex-col items-start gap-2">
          <Badge variant={account.active ? "secondary" : "outline"}>{account.active ? "Active" : "Deactivated"}</Badge>
          {!self && (
            <ToastForm action={setActive} submit={account.active ? "Deactivate" : "Reactivate"} pendingLabel="Saving…">
              <input type="hidden" name="user_id" value={account.id} />
              <input type="hidden" name="active" value={account.active ? "false" : "true"} />
            </ToastForm>
          )}
        </div>
      </TableCell>
      <TableCell className="align-top">
        <ToastForm action={setPassword} submit="Set" pendingLabel="Setting…" className="flex gap-2">
          <input type="hidden" name="user_id" value={account.id} />
          <Input name="password" type="password" required minLength={12} autoComplete="new-password"
                 placeholder={account.hasPassword ? "New password" : "No password yet"}
                 aria-label={`New password for ${account.email}`} className="w-40" />
        </ToastForm>
      </TableCell>
    </TableRow>
  );
}
