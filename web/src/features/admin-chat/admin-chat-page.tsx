import { useCallback, useEffect, useRef, useState } from "react";
import type { UIMessage } from "ai";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/layout/app-shell";
import { ActionCard } from "@/features/admin-chat/action-card";
import { ChatComposer } from "@/features/admin-chat/chat-composer";
import { ChatThread, useChatHooks, useChatSessions } from "@/features/admin-chat/chat-session";
import { seedMessages, type ChatHooks } from "@/features/admin-chat/chat-transport";
import { RunPanel } from "@/features/admin-chat/run-panel";
import type { ChatActionCard, ChatRun, ChatThreadSummary } from "@/schemas/admin-chat";
import { adminChatService } from "@/services/admin-chat-service";

/**
 * The admin chat page.
 *
 * The assistant half is Assistant UI on the Vercel AI SDK's runtime, one runtime
 * per thread (`chat-session.tsx`), with a transport aimed at the same-origin
 * `/api/admin/chat/completions` route the web server already proxies to FastAPI.
 * The JSON half here is the thread list, the action cards, and the run panels.
 */
export function AdminChatPage({ chatEnabled }: { chatEnabled: boolean }) {
  if (!chatEnabled) return <AdminChatDisabled />;
  return <AdminChat />;
}

export function AdminChatDisabled() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-3 px-8 py-10">
      <h1 className="text-headline-md text-foreground">
        Hermes chat is turned off on this server.
      </h1>
      <p className="text-body-md text-muted-foreground">
        An operator can turn it on by setting <code>HERMES_CHAT_ENABLED=true</code> and restarting
        the API. The Optimization screen works either way.
      </p>
    </div>
  );
}

export function AdminChatDenied() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-3 px-8 py-10">
      <h1 className="text-headline-md text-foreground">Hermes chat</h1>
      <p>This page is only available to admins.</p>
    </div>
  );
}

export function AdminChatMisconfigured({ message }: { message: string }) {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-3 px-8 py-10" role="alert">
      <h1 className="text-headline-md text-foreground">
        Hermes chat is turned on but not configured.
      </h1>
      <p className="text-body-md text-muted-foreground">{message}</p>
    </div>
  );
}

interface ThreadState {
  /** The thread this state belongs to, so a seed is never used for another one. */
  threadId: string | null;
  /** The stored history the thread's runtime was seeded from, or null while loading. */
  seed: UIMessage[] | null;
  /** A stored turn still running when the page loaded, resumed on mount (CHAT-005). */
  runningTurnId: string | null;
  actions: ChatActionCard[];
  runs: ChatRun[];
}

const LOADING: ThreadState = {
  threadId: null,
  seed: null,
  runningTurnId: null,
  actions: [],
  runs: [],
};

function AdminChat() {
  const [threads, setThreads] = useState<ChatThreadSummary[]>([]);
  const [activeThread, setActiveThread] = useState<string | null>(null);
  const [state, setState] = useState<ThreadState>(LOADING);
  const [failure, setFailure] = useState<string | null>(null);
  const sessions = useChatSessions();
  const loaded = useRef(new Map<string, ThreadState>());
  const emptyComposer = useRef<HTMLTextAreaElement | null>(null);

  const refreshThreads = useCallback(async () => {
    try {
      const items = await adminChatService.threads();
      setThreads(items);
      return items;
    } catch (error) {
      setFailure(message(error));
      return [];
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void refreshThreads().then((items) => {
      if (cancelled) return;
      setActiveThread((current) => current ?? items[0]?.thread_id ?? null);
    });
    return () => {
      cancelled = true;
    };
  }, [refreshThreads]);

  // Each thread's history is fetched once per page load. The runtime keeps the
  // messages after that, so re-seeding on every switch would be the duplicate
  // this page used to show.
  useEffect(() => {
    if (!activeThread) {
      setState(LOADING);
      return;
    }
    const cached = loaded.current.get(activeThread);
    if (cached) {
      setState(cached);
      return;
    }
    let cancelled = false;
    setState(LOADING);
    void loadThread(activeThread)
      .then((next) => {
        if (cancelled) return;
        loaded.current.set(activeThread, next);
        setState(next);
      })
      .catch((error: unknown) => {
        if (!cancelled) setFailure(message(error));
      });
    return () => {
      cancelled = true;
    };
  }, [activeThread]);

  const hooks = useChatHooks({
    onTool: () => undefined,
    onTurn: (frame) => {
      setState((current) => ({
        ...current,
        actions: mergeActions(current.actions, frame.actions),
      }));
    },
    onError: (text) => setFailure(text),
    onFinished: () => {
      void refreshThreads();
    },
  } satisfies ChatHooks);

  const threadId = activeThread;
  // The session is only built from a seed that belongs to this thread: a seed
  // still in state from the previous one must never create the next thread's
  // chat, which is what made one conversation's messages appear in another.
  const session =
    threadId && state.threadId === threadId && state.seed
      ? sessions.ensure(threadId, state.seed, hooks)
      : null;

  const newThread = useCallback(async () => {
    try {
      const created = await adminChatService.createThread();
      const fresh: ThreadState = { ...LOADING, threadId: created.thread_id, seed: [] };
      loaded.current.set(created.thread_id, fresh);
      setActiveThread(created.thread_id);
      setState(fresh);
      await refreshThreads();
    } catch (error) {
      setFailure(message(error));
    }
  }, [refreshThreads]);

  const deleteThread = useCallback(
    async (threadIdToDelete: string) => {
      try {
        await adminChatService.deleteThread(threadIdToDelete);
        sessions.drop(threadIdToDelete);
        loaded.current.delete(threadIdToDelete);
        const items = await refreshThreads();
        setActiveThread(items[0]?.thread_id ?? null);
      } catch (error) {
        setFailure(message(error));
      }
    },
    [refreshThreads, sessions],
  );

  const confirm = useCallback(
    async (card: ChatActionCard) => {
      try {
        const run = await adminChatService.confirmAction(card.action_id);
        setState((current) => ({
          ...current,
          actions: current.actions.map((item) =>
            item.action_id === card.action_id ? { ...item, status: "confirmed" as const } : item,
          ),
        }));
        if (activeThread) {
          await adminChatService.linkRun(activeThread, run.run_id);
          const runs = await adminChatService.runs(activeThread);
          setState((current) => ({ ...current, runs }));
        }
      } catch (error) {
        setFailure(message(error));
      }
    },
    [activeThread],
  );

  const dismiss = useCallback(async (card: ChatActionCard) => {
    try {
      await adminChatService.rejectAction(card.action_id);
      setState((current) => ({
        ...current,
        actions: current.actions.map((item) =>
          item.action_id === card.action_id ? { ...item, status: "rejected" as const } : item,
        ),
      }));
    } catch (error) {
      setFailure(message(error));
    }
  }, []);

  if (failure?.includes("not configured")) {
    return <AdminChatMisconfigured message={failure} />;
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-8 py-10">
      <PageHeader
        title="Hermes chat"
        description="Ask about this server's runs and champion, or ask the assistant to propose a run."
      />
      <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
        <ThreadList
          threads={threads}
          active={activeThread}
          onSelect={setActiveThread}
          onNew={newThread}
          onDelete={deleteThread}
        />
        <div className="flex flex-col gap-4">
          {session && threadId ? (
            <ChatThread
              // Keying by thread is what isolates the two runtimes: switching
              // threads unmounts one view and mounts the other, while each
              // session's `Chat` keeps its own messages and its own stream.
              key={threadId}
              session={session}
              runningTurnId={state.runningTurnId}
              disabled={false}
            />
          ) : (
            <div className="flex min-h-[32rem] flex-col gap-4">
              <p className="text-body-md text-muted-foreground">
                {activeThread
                  ? "Loading this thread…"
                  : "No messages yet. Ask a question to start."}
              </p>
              <ChatComposer
                busy={false}
                inputRef={emptyComposer}
                onSend={() => undefined}
                onStop={() => undefined}
                disabled
              />
            </div>
          )}
          {state.actions.length ? (
            <div className="flex flex-col gap-3">
              {state.actions.map((card) => (
                <ActionCard
                  key={card.action_id}
                  card={card}
                  onConfirm={confirm}
                  onDismiss={dismiss}
                />
              ))}
            </div>
          ) : null}
          {state.runs.map((run) => (
            <RunPanel key={run.run_id} run={run} />
          ))}
          {failure ? (
            <p role="alert" className="text-label-sm text-destructive">
              {failure}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

async function loadThread(threadId: string): Promise<ThreadState> {
  const [detail, runs] = await Promise.all([
    adminChatService.thread(threadId),
    adminChatService.runs(threadId).catch(() => [] as ChatRun[]),
  ]);
  const running = [...detail.turns].reverse().find((turn) => turn.status === "running");
  return {
    threadId,
    seed: seedMessages(detail.messages),
    runningTurnId: running?.turn_id ?? null,
    actions: detail.actions.map(toCard).filter((card): card is ChatActionCard => card !== null),
    runs,
  };
}

function mergeActions(current: ChatActionCard[], next: ChatActionCard[]): ChatActionCard[] {
  const byId = new Map(current.map((card) => [card.action_id, card]));
  for (const card of next) byId.set(card.action_id, card);
  return [...byId.values()];
}

function ThreadList({
  threads,
  active,
  onSelect,
  onNew,
  onDelete,
}: {
  threads: ChatThreadSummary[];
  active: string | null;
  onSelect: (threadId: string) => void;
  onNew: () => Promise<void>;
  onDelete: (threadId: string) => Promise<void>;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Threads</CardTitle>
        <Button size="sm" onClick={() => void onNew()}>
          New chat
        </Button>
      </CardHeader>
      <CardContent>
        <ul aria-label="Chat threads" className="flex flex-col gap-1">
          {threads.length === 0 ? (
            <li className="text-label-sm text-muted-foreground">No threads yet.</li>
          ) : null}
          {threads.map((thread) => (
            <li key={thread.thread_id} className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => onSelect(thread.thread_id)}
                aria-current={thread.thread_id === active ? "true" : undefined}
                className="flex-1 truncate rounded-md px-2 py-1 text-left text-label-sm hover:bg-accent"
              >
                {thread.title || "Untitled thread"}
              </button>
              {thread.last_turn_status === "running" ? <Badge>Running</Badge> : null}
              <DeleteThreadButton thread={thread} onDelete={onDelete} />
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function DeleteThreadButton({
  thread,
  onDelete,
}: {
  thread: ChatThreadSummary;
  onDelete: (threadId: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        size="sm"
        variant="ghost"
        aria-label={`Delete thread ${thread.title || "Untitled thread"}`}
        onClick={() => setOpen(true)}
      >
        Delete
      </Button>
      {open ? (
        <div role="dialog" aria-label="Delete this thread" className="rounded-md border p-3">
          <p className="text-label-sm">
            This deletes the conversation, its messages, and any pending action cards. The record of
            any run started from it — who started it, not what was said — is kept.
          </p>
          <div className="mt-2 flex gap-2">
            <Button
              size="sm"
              onClick={() => {
                setOpen(false);
                void onDelete(thread.thread_id);
              }}
            >
              Delete thread
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
    </>
  );
}

function toCard(action: {
  action_id: string;
  turn_id: string;
  kind: string;
  payload: string;
  status: string;
  created_at: string;
  expires_at: string;
}): ChatActionCard | null {
  if (action.kind !== "start_optimization_run" && action.kind !== "cancel_optimization_run") {
    return null;
  }
  let payload: { reason?: string; run_id?: string } = {};
  try {
    payload = JSON.parse(action.payload) as { reason?: string; run_id?: string };
  } catch {
    payload = {};
  }
  return {
    action_id: action.action_id,
    turn_id: action.turn_id,
    kind: action.kind,
    payload,
    status: action.status as ChatActionCard["status"],
    created_at: action.created_at,
    expires_at: action.expires_at,
  };
}

function message(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : "Something went wrong. Try again.";
}
