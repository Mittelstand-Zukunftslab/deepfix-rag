import type { ReactNode } from "react";

export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground"
      >
        Skip to content
      </a>
      <main id="content" className="flex flex-1 items-center justify-center px-4 py-12">
        <div className="w-full max-w-md">
          <div className="mb-8 flex items-center gap-2">
            <div className="size-3 rounded-sm bg-primary" aria-hidden />
            <span className="font-display text-xl font-semibold text-foreground">SearchDocs</span>
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}
