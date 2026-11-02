import { BarChart3, ClipboardCheck, FileText, FolderSearch, LayoutDashboard, ScrollText, Upload } from "lucide-react";

export type NavItem = {
  href: string;
  label: string;
  icon: typeof LayoutDashboard;
  permission: string; // shown only to roles holding it; the API enforces it regardless
  ready: boolean; // false until its Phase 5 step lands
};

export const NAV: NavItem[] = [
  { href: "/", label: "Overview", icon: LayoutDashboard, permission: "case:read", ready: true },
  { href: "/cases", label: "Cases", icon: FolderSearch, permission: "case:read", ready: true },
  { href: "/approvals", label: "Approvals", icon: ClipboardCheck, permission: "case:decide", ready: true },
  { href: "/data", label: "Data & runs", icon: Upload, permission: "data:import", ready: true },
  { href: "/policies", label: "Policies", icon: FileText, permission: "policy:read", ready: true },
  { href: "/reports", label: "Reports", icon: BarChart3, permission: "case:read", ready: true },
  { href: "/audit", label: "Audit trail", icon: ScrollText, permission: "audit:read", ready: true },
];
