import { Bot, PanelLeft, PanelLeftClose, PanelLeftOpen } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { useIsMobile } from "@/hooks/use-mobile";

/** The name the header and message labels call the assistant (D-7: no model id here). */
export const ASSISTANT_NAME = "HERMES chat";

export function ChatHeader({
  title,
  collapsed,
  drawerOpen,
  onToggleSidebar,
  onDrawerOpenChange,
  drawer,
}: {
  title: string;
  collapsed: boolean;
  drawerOpen: boolean;
  onToggleSidebar: () => void;
  onDrawerOpenChange: (open: boolean) => void;
  drawer: React.ReactNode;
}) {
  const isMobile = useIsMobile();
  const ToggleIcon = collapsed ? PanelLeftOpen : PanelLeftClose;

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-card/60 px-3 backdrop-blur">
      <Button
        variant="ghost"
        size="icon"
        onClick={onToggleSidebar}
        aria-label={collapsed ? "Show conversations" : "Hide conversations"}
        aria-expanded={!collapsed}
        className="shrink-0"
      >
        <ToggleIcon className="size-4" aria-hidden />
      </Button>

      <div className="flex min-w-0 items-center gap-2">
        <span className="flex size-6 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Bot className="size-3.5" aria-hidden />
        </span>
        <h1 className="truncate text-body-md font-medium text-foreground" title={title}>
          {title}
        </h1>
      </div>

      <span className="ms-auto shrink-0 rounded-full border border-border px-2.5 py-1 text-label-sm text-muted-foreground">
        {ASSISTANT_NAME}
      </span>

      {isMobile ? (
        <Sheet open={drawerOpen} onOpenChange={onDrawerOpenChange}>
          <SheetContent side="left" className="w-72 border-sidebar-border bg-sidebar p-0">
            <SheetTitle className="sr-only">Conversations</SheetTitle>
            {drawer}
          </SheetContent>
        </Sheet>
      ) : null}
    </header>
  );
}

/** The header control's icon for the collapsed state, exported for the sidebar's own rail. */
export const SidebarToggleIcon = PanelLeft;
