import {
  Activity,
  Bot,
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

const ADMIN_PATHS: ReadonlySet<AppPath> = new Set(["/history", "/admin", "/admin/chat"]);

const NAV_ITEMS: NavItem[] = [
  { to: "/search", label: "Search", icon: Search },
  { to: "/chat", label: "Ask", icon: MessageSquare },
  { to: "/upload", label: "Library", icon: Library },
  { to: "/history", label: "History", icon: History },
  { to: "/admin", label: "Optimization", icon: ShieldCheck },
  { to: "/admin/chat", label: "Hermes chat", icon: Bot },
  { to: "/status", label: "Status", icon: Activity },
];

export function navigationItems(role: AccountRole | null | undefined): NavItem[] {
  return NAV_ITEMS.filter((item) => !ADMIN_PATHS.has(item.to) || role === "admin");
}

/**
 * True when `to` is the item the current path belongs to.
 *
 * The most specific match wins: at `/admin/chat` both `/admin` and
 * `/admin/chat` are prefixes, and only the second is the current page. A path
 * is a match when it equals `to`, or when it continues with a `/`, so `/adminx`
 * never matches `/admin`.
 */
export function activePathMatches(active: string, items: NavItem[], to: AppPath): boolean {
  const owner = items
    .filter((item) => active === item.to || active.startsWith(`${item.to}/`))
    .sort((left, right) => right.to.length - left.to.length)[0];
  return owner?.to === to;
}
