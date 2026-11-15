import { Bot, Moon, PanelLeftClose, PanelLeftOpen, Sun } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { useIsMobile } from "@/hooks/use-mobile";

/** The name the header and message labels call the assistant (D-7: no model id here). */
export const ASSISTANT_NAME = "HERMES chat";

export function ChatHeader({
  title,
  collapsed,
  drawerOpen,
  appearance,
  onToggleSidebar,
  onDrawerOpenChange,
  onToggleAppearance,
  drawer,
}: {
  title: string;
  collapsed: boolean;
  drawerOpen: boolean;
  appearance: "light" | "dark";
  onToggleSidebar: () => void;
  onDrawerOpenChange: (open: boolean) => void;
  onToggleAppearance: () => void;
  drawer: ReactNode;
}) {
  const isMobile = useIsMobile();
  const ToggleIcon = collapsed ? PanelLeftOpen : PanelLeftClose;
  const dark = appearance === "dark";
  // Below `md` the sidebar is a drawer, so the control opens and closes it rather
  // than collapsing a column that is not on screen; the label has to say which.
  const sidebarLabel = isMobile
    ? drawerOpen
      ? "Hide conversations"
      : "Show conversations"
    : collapsed
      ? "Show conversations"
      : "Hide conversations";

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-card/60 px-3 backdrop-blur">
      <Button
        variant="ghost"
        size="icon"
        onClick={onToggleSidebar}
        aria-label={sidebarLabel}
        aria-expanded={isMobile ? drawerOpen : !collapsed}
        aria-controls="admin-chat-sidebar"
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

      <Button
        variant="ghost"
        size="icon"
        onClick={onToggleAppearance}
        aria-pressed={dark}
        aria-label={dark ? "Switch to light appearance" : "Switch to dark appearance"}
        className="shrink-0"
      >
        {dark ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
      </Button>

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
