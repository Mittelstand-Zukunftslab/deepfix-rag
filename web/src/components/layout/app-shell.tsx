import type { ReactNode } from "react";

import { SignOutButton } from "@/features/auth/sign-out-button";
import type { Account } from "@/schemas/auth";
import { AppSidebar } from "./app-sidebar";

export function AppShell({ children, user }: { children: ReactNode; user: Account }) {
  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground"
      >
        Skip to content
      </a>
      <AppSidebar role={user.role} />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-end gap-4 border-b border-border bg-card px-6">
          <p className="min-w-0 truncate text-sm text-foreground" title={user.email}>
            {user.email}
          </p>
          <SignOutButton />
        </header>
        <main id="content" className="flex min-h-0 flex-1 flex-col overflow-auto">
          {children}
        </main>
      </div>
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
