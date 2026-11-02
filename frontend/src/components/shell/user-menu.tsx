import { LogOut } from "lucide-react";

import { logout } from "@/app/actions/auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { CurrentUser } from "@/lib/dal";

export function UserMenu({ user }: { user: CurrentUser }) {
  return (
    <div className="flex items-center gap-3">
      <div className="text-right leading-tight">
        <div className="text-sm font-medium">{user.full_name}</div>
        <div className="text-xs text-muted-foreground">{user.email}</div>
      </div>
      {user.roles.map((role) => (
        <Badge key={role} variant="secondary" className="capitalize">{role}</Badge>
      ))}
      <form action={logout}>
        <Button type="submit" variant="ghost" size="icon" aria-label="Sign out" title="Sign out">
          <LogOut className="size-4" />
        </Button>
      </form>
    </div>
  );
}
