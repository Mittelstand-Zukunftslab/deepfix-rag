import {
  Activity,
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

const NAV_ITEMS: NavItem[] = [
  { to: "/search", label: "Search", icon: Search },
  { to: "/chat", label: "Ask", icon: MessageSquare },
  { to: "/upload", label: "Library", icon: Library },
  { to: "/admin", label: "Admin", icon: ShieldCheck, badge: "OPS" },
  { to: "/status", label: "Status", icon: Activity },
];

export function navigationItems(role: AccountRole | null | undefined): NavItem[] {
  return NAV_ITEMS.filter((item) => item.to !== "/admin" || role === "admin");
}
