"use client";

import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The composer surface both chats share.
 *
 * What travels is the bar and the hint: the rounded surface, its border, its
 * focus ring, and the one-line note underneath, so the Ask page and the admin
 * Hermes chat are visibly the same composer. The send control does not travel,
 * because the two are different objects: the Ask page's request cannot be
 * cancelled and its button shows a spinner, while the admin chat's button has to
 * become **Stop** mid-stream (CHAT-018). Both are built inside this bar.
 */
export function ChatComposerBar({
  className,
  children,
  ...props
}: ComponentPropsWithoutRef<"div">) {
  return (
    <div
      data-slot="chat-composer-bar"
      className={cn(
        "relative flex items-center gap-2 rounded-2xl border border-input bg-card/90 px-3 py-1.5 shadow-sm",
        "transition-all focus-within:border-primary/60 focus-within:ring-2 focus-within:ring-primary/20",
        "hover:border-border",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

/** The one line under the bar. Not a control, and never a promise that a run starts. */
export function ChatComposerHint({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <p
      data-slot="composer-hint"
      className={cn("mt-1.5 text-center text-label-sm text-muted-foreground", className)}
    >
      {children}
    </p>
  );
}
