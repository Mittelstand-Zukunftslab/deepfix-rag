import { useState } from "react";
import { MessageSquarePlus, Trash2 } from "lucide-react";

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
import { cn } from "@/lib/utils";

/**
 * The member's conversation list.
 *
 * The cap is a display rule, so the copy says "most recent" rather than implying
 * the older ones are gone: a thread past the 50-item window still exists and is
 * still reachable by id (D-11). No "load more" is offered, because the server has
 * no pagination to page through.
 *
 * The delete dialog's distinction is the product promise D-9 makes: the
 * conversation goes and the votes it recorded stay. A member told only "delete"
 * would reasonably expect their feedback to vanish too.
 */

export const DELETE_CONVERSATION_COPY =
  "This deletes the conversation and its messages. The votes you recorded on its answers and passages are kept, because they are the feedback the optimizer learns from.";

const CAP = 50;

export function AskThreadSidebar({
  threads,
  activeThreadId,
  error,
  onCreate,
  onOpen,
  onRename,
  onDelete,
}: {
  threads: { thread_id: string; title: string }[];
  activeThreadId: string | null;
  error: string | null;
  onCreate: () => void;
  onOpen: (threadId: string) => void;
  onRename: (threadId: string, title: string) => Promise<void> | void;
  onDelete: (threadId: string) => Promise<void> | void;
}) {
  return (
    <aside className="hidden w-64 shrink-0 flex-col gap-2 rounded-2xl border border-border/70 bg-card/60 p-3 backdrop-blur-md md:flex">
      <div className="flex items-center justify-between px-1 pb-1">
        <h2 className="text-label-sm font-semibold uppercase tracking-wider text-muted-foreground">
          Conversations
        </h2>
      </div>
      <Button
        variant="outline"
        className="w-full justify-start gap-2 border-dashed border-primary/40 bg-primary/5 text-label-md font-medium text-foreground hover:border-primary hover:bg-primary/10"
        onClick={onCreate}
      >
        <MessageSquarePlus className="size-4 text-primary" aria-hidden />
        New conversation
      </Button>
      {error ? (
        <p role="alert" className="px-1 text-label-sm text-destructive">
          {error}
        </p>
      ) : null}
      {threads.length === 0 ? (
        <p className="px-1 py-4 text-label-sm text-muted-foreground">
          No conversations yet. Start one to ask about the documents.
        </p>
      ) : (
        <nav aria-label="Conversations" className="min-h-0 flex-1 overflow-y-auto">
          <ul className="flex flex-col gap-0.5">
            {threads.map((thread) => (
              <li key={thread.thread_id}>
                <ThreadRow
                  thread={thread}
                  active={thread.thread_id === activeThreadId}
                  onOpen={onOpen}
                  onRename={onRename}
                  onDelete={onDelete}
                />
              </li>
            ))}
          </ul>
        </nav>
      )}
      {threads.length === CAP ? (
        <p className="px-1 text-label-sm text-muted-foreground">
          These are your most recent {CAP} conversations. Older ones are still saved.
        </p>
      ) : null}
    </aside>
  );
}

function ThreadRow({
  thread,
  active,
  onOpen,
  onRename,
  onDelete,
}: {
  thread: { thread_id: string; title: string };
  active: boolean;
  onOpen: (threadId: string) => void;
  onRename: (threadId: string, title: string) => Promise<void> | void;
  onDelete: (threadId: string) => Promise<void> | void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(thread.title);
  const title = thread.title || "Untitled conversation";

  const save = () => {
    setEditing(false);
    const next = draft.trim();
    if (next && next !== thread.title) void onRename(thread.thread_id, next);
  };

  return (
    <div className="group/thread relative flex items-center">
      {editing ? (
        <form
          className="flex w-full items-center gap-1 py-1 pe-1 ps-2"
          onSubmit={(event) => {
            event.preventDefault();
            save();
          }}
        >
          <label htmlFor={`rename-${thread.thread_id}`} className="sr-only">
            Conversation title
          </label>
          <input
            id={`rename-${thread.thread_id}`}
            autoFocus
            value={draft}
            maxLength={120}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={save}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                setDraft(thread.title);
                setEditing(false);
              }
            }}
            className="min-w-0 flex-1 rounded-md border border-input bg-background px-2 py-1 text-body-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <button type="submit" className="sr-only">
            Save title
          </button>
        </form>
      ) : (
        <>
          <button
            type="button"
            onClick={() => onOpen(thread.thread_id)}
            onDoubleClick={() => {
              setDraft(thread.title);
              setEditing(true);
            }}
            aria-current={active ? "true" : undefined}
            className={cn(
              "flex h-9 w-full items-center gap-2 rounded-lg px-2 pe-16 text-start text-body-sm transition-colors",
              "hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              active ? "bg-accent font-medium text-accent-foreground" : "text-foreground",
            )}
          >
            <span className="truncate">{title}</span>
          </button>
          <div
            className={cn(
              "absolute end-1 flex items-center gap-0.5 opacity-0 transition-opacity",
              "group-hover/thread:opacity-100 group-focus-within/thread:opacity-100",
              confirming && "opacity-100",
            )}
          >
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Rename ${title}`}
              onClick={() => {
                setDraft(thread.title);
                setEditing(true);
              }}
              className="size-7 text-muted-foreground"
            >
              <span aria-hidden className="text-label-sm">
                Edit
              </span>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Delete ${title}`}
              onClick={() => setConfirming(true)}
              className="size-7 text-muted-foreground hover:text-destructive"
            >
              <Trash2 className="size-3.5" aria-hidden />
            </Button>
          </div>
        </>
      )}
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this conversation</AlertDialogTitle>
            <AlertDialogDescription>{DELETE_CONVERSATION_COPY}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirming(false);
                void onDelete(thread.thread_id);
              }}
            >
              Delete conversation
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
