import {
  Activity,
  History,
  Library,
  MessageSquare,
  Search,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";

import type { AppPath } from "@/components/common/nav-link";
import type { AccountRole } from "@/schemas/auth";

export interface NavItem {
  to: AppPath;
  label: string;
  icon: LucideIcon;
  badge?: string;
}

const ADMIN_PATHS: ReadonlySet<AppPath> = new Set(["/history", "/admin"]);

const NAV_ITEMS: NavItem[] = [
  { to: "/search", label: "Search", icon: Search },
  { to: "/chat", label: "Ask", icon: MessageSquare },
  { to: "/upload", label: "Library", icon: Library },
  { to: "/history", label: "History", icon: History },
  { to: "/admin", label: "Optimization", icon: ShieldCheck },
  { to: "/status", label: "Status", icon: Activity },
];

export function navigationItems(role: AccountRole | null | undefined): NavItem[] {
  return NAV_ITEMS.filter((item) => !ADMIN_PATHS.has(item.to) || role === "admin");
}
