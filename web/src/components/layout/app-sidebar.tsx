import { Menu, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useState } from "react";

import { NavLink, useActivePath } from "@/components/common/nav-link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";
import type { AccountRole } from "@/schemas/auth";
import { useSidebarStore } from "@/stores/sidebar-store";
import { activePathMatches, navigationItems } from "./navigation";

/**
 * The app's navigation.
 *
 * Below the `md` breakpoint it is a drawer rather than a column: a fixed 288px
 * sidebar on a 390px phone left the page beside it unusable, which is what the
 * chat's own mobile layout exposed. Above `md` it collapses to icons exactly as
 * before.
 */
export function AppSidebar({ role }: { role: AccountRole | null }) {
  const isMobile = useIsMobile();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const items = navigationItems(role);

  if (isMobile) {
    return (
      <>
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-card px-3 md:hidden">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setDrawerOpen(true)}
            aria-label="Open menu"
          >
            <Menu className="size-5" aria-hidden />
          </Button>
          <span className="font-display text-body-md font-semibold">SearchDocs</span>
        </div>
        <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
          <SheetContent side="left" className="w-72 border-sidebar-border bg-sidebar p-0">
            <SheetTitle className="sr-only">Navigation</SheetTitle>
            <SidebarBody items={items} collapsed={false} onNavigate={() => setDrawerOpen(false)} />
          </SheetContent>
        </Sheet>
      </>
    );
  }

  return (
    <aside className="hidden h-full shrink-0 md:flex">
      <SidebarBody items={items} collapsible />
    </aside>
  );
}

function SidebarBody({
  items,
  collapsible = false,
  onNavigate,
}: {
  items: ReturnType<typeof navigationItems>;
  collapsed?: boolean;
  collapsible?: boolean;
  onNavigate?: () => void;
}) {
  const active = useActivePath();
  const isCollapsed = useSidebarStore((state) => state.isCollapsed);
  const toggleSidebar = useSidebarStore((state) => state.toggleSidebar);
  const collapsed = collapsible ? isCollapsed : false;
  const ToggleIcon = collapsed ? PanelLeftOpen : PanelLeftClose;

  return (
    <div
      className={cn(
        "flex h-full flex-col overflow-hidden bg-sidebar py-6 transition-[width] duration-200 ease-out",
        collapsed ? "w-20 px-3" : "w-72 px-5",
      )}
    >
      <div
        className={cn(
          "flex gap-2 pb-8",
          collapsed ? "flex-col items-center" : "items-center justify-between px-1",
        )}
      >
        <div className="flex items-center gap-2">
          <div className="size-3 rounded-sm bg-primary" />
          {collapsed ? null : (
            <span className="font-display text-xl font-semibold text-foreground">SearchDocs</span>
          )}
        </div>
        {collapsible ? (
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleSidebar}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-expanded={!collapsed}
          >
            <ToggleIcon />
          </Button>
        ) : null}
      </div>
      <nav className="flex flex-col gap-1">
        {items.map((item) => {
          // Longest matching prefix wins, so `/admin/chat` does not also light
          // `/admin`: a plain `startsWith` would mark both as the current page.
          const isActive = activePathMatches(active, items, item.to);
          return (
            <NavLink
              key={item.to}
              to={item.to}
              aria-label={collapsed ? item.label : undefined}
              title={collapsed ? item.label : undefined}
              className={cn(
                "flex items-center gap-3 rounded-lg py-2.5 text-label-lg transition-colors",
                collapsed ? "justify-center px-0" : "px-3",
                isActive
                  ? "bg-sidebar-primary text-sidebar-primary-foreground shadow-soft"
                  : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
              )}
            >
              <span onClick={onNavigate} className="contents">
                <item.icon className="size-5 shrink-0" />
                {collapsed ? null : (
                  <>
                    <span className="flex-1 whitespace-nowrap">{item.label}</span>
                    {item.badge ? <Badge variant="soft">{item.badge}</Badge> : null}
                  </>
                )}
              </span>
            </NavLink>
          );
        })}
      </nav>
    </div>
  );
}
