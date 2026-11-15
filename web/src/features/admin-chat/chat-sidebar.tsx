import { useState } from "react";
import { Bot, Loader2, MessageSquarePlus, Trash2 } from "lucide-react";

import { SignOutButton } from "@/features/auth/sign-out-button";
import { type ThreadGroup } from "@/features/admin-chat/thread-groups";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { ChatThreadSummary } from "@/schemas/admin-chat";

/** The delete confirmation's copy is the product promise CHAT-010 fixed; do not shorten it. */
export const DELETE_THREAD_COPY =
  "This deletes the conversation, its messages, and any pending action cards. The record of any run started from it — who started it, not what was said — is kept.";

export function ChatSidebar({
  groups,
  loading,
  activeThreadId,
  email,
  onSelect,
  onNew,
  onDelete,
}: {
  groups: ThreadGroup[];
  loading: boolean;
  activeThreadId: string | null;
  email: string;
  onSelect: (threadId: string) => void;
  onNew: () => Promise<void>;
  onDelete: (threadId: string) => Promise<void>;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col bg-sidebar">
      <div className="flex h-14 shrink-0 items-center gap-2 px-3">
        <div className="flex size-7 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
          <Bot className="size-4" aria-hidden />
        </div>
        <span className="font-display text-body-md font-semibold">Hermes</span>
      </div>
      <div className="px-3 pb-2">
        <Button
          variant="outline"
          className="w-full justify-start gap-2 border-sidebar-border bg-transparent"
          onClick={() => void onNew()}
        >
          <MessageSquarePlus className="size-4" aria-hidden />
          New chat
        </Button>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <ThreadGroups
          groups={groups}
          loading={loading}
          activeThreadId={activeThreadId}
          onSelect={onSelect}
          onDelete={onDelete}
        />
      </ScrollArea>
      <div className="flex shrink-0 items-center gap-2 border-t border-sidebar-border p-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-label-md text-sidebar-foreground" title={email}>
            {email}
          </p>
          <p className="text-label-sm uppercase text-muted-foreground">Admin</p>
        </div>
        <SignOutButton variant="ghost" size="sm" />
      </div>
    </div>
  );
}

export function ThreadGroups({
  groups,
  loading,
  activeThreadId,
  onSelect,
  onDelete,
}: {
  groups: ThreadGroup[];
  loading: boolean;
  activeThreadId: string | null;
  onSelect: (threadId: string) => void;
  onDelete: (threadId: string) => Promise<void>;
}) {
  if (loading) {
    return (
      <div className="flex flex-col gap-2 p-3" role="status" aria-label="Loading threads">
        {["w-4/5", "w-3/5", "w-2/3"].map((width) => (
          <Skeleton key={width} className={cn("h-7 rounded-md", width)} />
        ))}
      </div>
    );
  }
  if (groups.length === 0) {
    return (
      <p className="px-4 py-6 text-label-md text-muted-foreground">
        No conversations yet. Start one with New chat.
      </p>
    );
  }
  return (
    <nav aria-label="Chat threads" className="flex flex-col gap-4 px-3 py-2">
      {groups.map((group) => (
        <section key={group.label} aria-label={group.label} className="flex flex-col gap-0.5">
          <h2 className="px-2 pb-1 text-label-sm uppercase text-muted-foreground">{group.label}</h2>
          <ul className="flex flex-col gap-0.5">
            {group.threads.map((thread) => (
              <li key={thread.thread_id}>
                <ThreadRow
                  thread={thread}
                  active={thread.thread_id === activeThreadId}
                  onSelect={onSelect}
                  onDelete={onDelete}
                />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </nav>
  );
}

function ThreadRow({
  thread,
  active,
  onSelect,
  onDelete,
}: {
  thread: ChatThreadSummary;
  active: boolean;
  onSelect: (threadId: string) => void;
  onDelete: (threadId: string) => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const title = thread.title || "Untitled thread";
  const running = thread.last_turn_status === "running";

  return (
    <div className="group/thread relative">
      <button
        type="button"
        onClick={() => onSelect(thread.thread_id)}
        aria-current={active ? "true" : undefined}
        className={cn(
          "flex h-9 w-full items-center gap-2 rounded-lg px-2 pe-9 text-start text-body-sm transition-colors",
          "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
          active
            ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
            : "text-sidebar-foreground",
        )}
      >
        {running ? (
          <Loader2
            className="size-3.5 shrink-0 animate-spin text-primary motion-reduce:animate-none"
            aria-hidden
          />
        ) : null}
        <span className="truncate">{title}</span>
        {running ? <span className="sr-only">Running</span> : null}
      </button>
      <Button
        variant="ghost"
        size="icon"
        aria-label={`Delete thread ${title}`}
        onClick={() => setConfirming(true)}
        className={cn(
          "absolute end-1 top-1/2 size-7 -translate-y-1/2 text-muted-foreground",
          "opacity-0 transition-opacity group-hover/thread:opacity-100 group-focus-within/thread:opacity-100",
          // Visible while its own dialog is open, so the row still shows what it belongs to.
          confirming && "opacity-100",
        )}
      >
        <Trash2 className="size-3.5" aria-hidden />
      </Button>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this thread</AlertDialogTitle>
            <AlertDialogDescription>{DELETE_THREAD_COPY}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirming(false);
                void onDelete(thread.thread_id);
              }}
            >
              Delete thread
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
