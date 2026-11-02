"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
import { NAV } from "./nav";

export function SidebarLinks({ permissions }: { permissions: string[] }) {
  const pathname = usePathname();
  const items = NAV.filter((item) => permissions.includes(item.permission));

  return (
    <nav aria-label="Main" className="flex flex-col gap-1">
      {items.map(({ href, label, icon: Icon, ready }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        if (!ready) {
          return (
            <span key={href} className="flex items-center gap-3 rounded-md px-3 py-2 text-sm text-muted-foreground/60"
                  aria-disabled="true" title="Coming in a later Phase 5 step">
              <Icon className="size-4" aria-hidden />
              {label}
              <span className="ml-auto text-[10px] uppercase tracking-wide">soon</span>
            </span>
          );
        }
        return (
          <Link key={href} href={href} aria-current={active ? "page" : undefined}
                className={cn("flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                  active ? "bg-accent font-medium text-accent-foreground"
                         : "text-muted-foreground hover:bg-accent/60 hover:text-foreground")}>
            <Icon className="size-4" aria-hidden />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
