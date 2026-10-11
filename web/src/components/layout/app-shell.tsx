import type { ReactNode } from "react";

import { AppSidebar } from "./app-sidebar";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-background">
      <AppSidebar />
      <main className="min-w-0 flex-1">{children}</main>
    </div>
  );
}

interface PageHeaderProps {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}

export function PageHeader({ eyebrow, title, description, actions }: PageHeaderProps) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-6 pb-8">
      <div className="flex max-w-2xl flex-col gap-2">
        {eyebrow ? <p className="text-label-sm uppercase text-primary">{eyebrow}</p> : null}
        <h1 className="text-headline-xl text-foreground">{title}</h1>
        {description ? <p className="text-body-md text-muted-foreground">{description}</p> : null}
      </div>
      {actions}
    </header>
  );
}

export function TopBar({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <div className="sticky top-0 z-10 flex h-16 items-center justify-between bg-card/80 px-8 backdrop-blur">
      <h1 className="text-headline-md text-foreground">{title}</h1>
      {actions}
    </div>
  );
}
