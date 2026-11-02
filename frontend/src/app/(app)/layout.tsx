import { Suspense } from "react";

import { SidebarLinks } from "@/components/shell/sidebar-links";
import { UserMenu } from "@/components/shell/user-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { getCurrentUser } from "@/lib/dal";

/**
 * The shell renders immediately; only the parts that need the session stream in behind Suspense. Each page also
 * calls the DAL itself, because a layout does not re-run on every navigation (see the Next.js auth guide).
 */
export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <div className="flex min-h-full flex-1">
      <aside className="hidden w-60 shrink-0 flex-col gap-6 border-r bg-muted/30 p-4 md:flex">
        <div className="px-3 text-base font-semibold tracking-tight">FinRecon AI</div>
        <Suspense fallback={<NavSkeleton />}>
          <Navigation />
        </Suspense>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-end border-b px-6">
          <Suspense fallback={<Skeleton className="h-8 w-56" />}>
            <CurrentUserMenu />
          </Suspense>
        </header>
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}

async function Navigation() {
  const user = await getCurrentUser();
  return <SidebarLinks permissions={user.permissions} />;
}

async function CurrentUserMenu() {
  return <UserMenu user={await getCurrentUser()} />;
}

function NavSkeleton() {
  return (
    <div className="flex flex-col gap-2 px-3">
      {Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-6 w-full" />)}
    </div>
  );
}
