import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

import { NavLink, useActivePath } from "@/components/common/nav-link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { AccountRole } from "@/schemas/auth";
import { useSidebarStore } from "@/stores/sidebar-store";
import { navigationItems } from "./navigation";

export function AppSidebar({ role }: { role: AccountRole | null }) {
  const active = useActivePath();
  const items = navigationItems(role);
  const isCollapsed = useSidebarStore((state) => state.isCollapsed);
  const toggleSidebar = useSidebarStore((state) => state.toggleSidebar);
  const ToggleIcon = isCollapsed ? PanelLeftOpen : PanelLeftClose;

  return (
    <aside
      className={cn(
        "flex h-full shrink-0 flex-col overflow-hidden bg-sidebar py-6 transition-[width] duration-200 ease-out",
        isCollapsed ? "w-20 px-3" : "w-72 px-5",
      )}
    >
      <div
        className={cn(
          "flex gap-2 pb-8",
          isCollapsed ? "flex-col items-center" : "items-center justify-between px-1",
        )}
      >
        <div className="flex items-center gap-2">
          <div className="size-3 rounded-sm bg-primary" />
          {isCollapsed ? null : (
            <span className="font-display text-xl font-semibold text-foreground">SearchDocs</span>
          )}
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleSidebar}
          aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!isCollapsed}
        >
          <ToggleIcon />
        </Button>
      </div>
      <nav className="flex flex-col gap-1">
        {items.map((item) => {
          const isActive = active.startsWith(item.to);
          return (
            <NavLink
              key={item.to}
              to={item.to}
              aria-label={isCollapsed ? item.label : undefined}
              title={isCollapsed ? item.label : undefined}
              className={cn(
                "flex items-center gap-3 rounded-lg py-2.5 text-label-lg transition-colors",
                isCollapsed ? "justify-center px-0" : "px-3",
                isActive
                  ? "bg-sidebar-primary text-sidebar-primary-foreground shadow-soft"
                  : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
              )}
            >
              <item.icon className="size-5 shrink-0" />
              {isCollapsed ? null : (
                <>
                  <span className="flex-1 whitespace-nowrap">{item.label}</span>
                  {item.badge ? <Badge variant="soft">{item.badge}</Badge> : null}
                </>
              )}
            </NavLink>
          );
        })}
      </nav>
    </aside>
  );
}
