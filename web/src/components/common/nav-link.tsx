// The ONLY module (with use-app-navigation.ts) that touches the router.
// For Next.js: replace with `next/link` + `usePathname()`.
import { Link, useRouterState } from "@tanstack/react-router";
import type { ReactNode } from "react";

export type AppPath =
  "/search" | "/chat" | "/upload" | "/admin" | "/status" | "/sign-in" | "/register";

interface NavLinkProps {
  to: AppPath;
  className?: string;
  title?: string | undefined;
  "aria-label"?: string | undefined;
  children: ReactNode;
}

export function NavLink({ to, className, title, "aria-label": ariaLabel, children }: NavLinkProps) {
  return (
    <Link to={to} className={className} title={title} aria-label={ariaLabel}>
      {children}
    </Link>
  );
}

export function useActivePath(): string {
  return useRouterState({ select: (s) => s.location.pathname });
}
