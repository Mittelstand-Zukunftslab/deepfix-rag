import { useCallback, useEffect, useRef, useState } from "react";
import type { UIMessage } from "ai";

import { ChatHeader } from "@/features/admin-chat/chat-header";
import { ChatSidebar } from "@/features/admin-chat/chat-sidebar";
import { ActionCard } from "@/features/admin-chat/action-card";
import { DraftComposer } from "@/features/admin-chat/chat-composer";
import { ChatThread, useChatHooks, useChatSessions } from "@/features/admin-chat/chat-session";
import { seedMessages, type ChatHooks } from "@/features/admin-chat/chat-transport";
import { RunPanel } from "@/features/admin-chat/run-panel";
import { groupThreadsByDate } from "@/features/admin-chat/thread-groups";
import { WelcomePanel } from "@/features/admin-chat/thread-welcome";
import { cn } from "@/lib/utils";
import type { ChatActionCard, ChatRun, ChatThreadSummary } from "@/schemas/admin-chat";
import { adminChatService } from "@/services/admin-chat-service";
import { useAdminChatStore } from "@/stores/admin-chat-store";

/**
 * The admin chat page.
 *
 * The assistant half is Assistant UI on the Vercel AI SDK's runtime, one runtime
 * per thread (`chat-session.tsx`), with a transport aimed at the same-origin
 * `/api/admin/chat/completions` route the web server already proxies to FastAPI.
 * The JSON half here is the thread list, the action cards, and the run panels.
 *
 * The thread list is drawn from `adminChatService.threads()` rather than from
 * Assistant UI's runtime thread list on purpose: a runtime thread list puts every
 * conversation on one shared runtime, which is exactly the shape CHAT-015
 * removed when it gave each thread its own `Chat`.
 */
export function AdminChatPage({
  chatEnabled,
  email = "",
}: {
  chatEnabled: boolean;
  /** The signed-in admin, shown in the sidebar's account footer. */
  email?: string;
}) {
  if (!chatEnabled) return <AdminChatDisabled />;
  return <AdminChat email={email} />;
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

function AdminChat({ email }: { email: string }) {
  const [threads, setThreads] = useState<ChatThreadSummary[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [activeThread, setActiveThread] = useState<string | null>(null);
  const [state, setState] = useState<ThreadState>(LOADING);
  const [failure, setFailure] = useState<string | null>(null);
  /** A chip's prompt, handed to the composer once its new thread is open. */
  const [pendingPrompt, setPendingPrompt] = useState<{ threadId: string; prompt: string } | null>(
    null,
  );
  const [drawerOpen, setDrawerOpen] = useState(false);
  const sessions = useChatSessions();
  const loaded = useRef(new Map<string, ThreadState>());
  const collapsed = useAdminChatStore((store) => store.sidebarCollapsed);
  const toggleSidebar = useAdminChatStore((store) => store.toggleSidebar);

  const refreshThreads = useCallback(async () => {
    try {
      const items = await adminChatService.threads();
      setThreads(items);
      return items;
    } catch (error) {
      setFailure(message(error));
      return [];
    } finally {
      setThreadsLoading(false);
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

  const selectThread = useCallback((next: string) => {
    setActiveThread(next);
    setDrawerOpen(false);
  }, []);

  const newThread = useCallback(
    async (prompt?: string) => {
      try {
        const created = await adminChatService.createThread();
        const fresh: ThreadState = { ...LOADING, threadId: created.thread_id, seed: [] };
        loaded.current.set(created.thread_id, fresh);
        setActiveThread(created.thread_id);
        setState(fresh);
        setDrawerOpen(false);
        // A chip fills the composer of the thread it opens; it never sends.
        setPendingPrompt(prompt ? { threadId: created.thread_id, prompt } : null);
        await refreshThreads();
      } catch (error) {
        setFailure(message(error));
      }
    },
    [refreshThreads],
  );

  const deleteThread = useCallback(
    async (threadIdToDelete: string) => {
      try {
        await adminChatService.deleteThread(threadIdToDelete);
        sessions.drop(threadIdToDelete);
        loaded.current.delete(threadIdToDelete);
        const items = await refreshThreads();
        setActiveThread((current) =>
          current === threadIdToDelete ? (items[0]?.thread_id ?? null) : current,
        );
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

  const activeTitle =
    threads.find((thread) => thread.thread_id === activeThread)?.title || "New conversation";
  const groups = groupThreadsByDate(threads);
  const sidebar = (
    <ChatSidebar
      groups={groups}
      loading={threadsLoading}
      activeThreadId={activeThread}
      email={email}
      onSelect={selectThread}
      onNew={newThread}
      onDelete={deleteThread}
    />
  );

  return (
    <div className="flex h-full min-h-0 w-full">
      <aside
        aria-label="Conversations"
        className={cn(
          "hidden shrink-0 border-e border-sidebar-border transition-[width] duration-200 ease-out md:block",
          collapsed ? "w-0 overflow-hidden border-e-0" : "w-72",
        )}
      >
        {sidebar}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <ChatHeader
          title={activeTitle}
          collapsed={collapsed}
          drawerOpen={drawerOpen}
          onToggleSidebar={() => {
            toggleSidebar();
            setDrawerOpen(false);
          }}
          onDrawerOpenChange={setDrawerOpen}
          drawer={sidebar}
        />

        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-6">
            {session && threadId ? (
              <ChatThread
                // Keying by thread is what isolates the two runtimes: switching
                // threads unmounts one view and mounts the other, while each
                // session's `Chat` keeps its own messages and its own stream.
                key={threadId}
                session={session}
                runningTurnId={state.runningTurnId}
                initialPrompt={
                  pendingPrompt?.threadId === threadId ? pendingPrompt.prompt : undefined
                }
                disabled={false}
              />
            ) : (
              <div className="flex min-h-0 flex-1 flex-col justify-end gap-4">
                {activeThread ? (
                  <p className="text-body-md text-muted-foreground">Loading this thread…</p>
                ) : (
                  <WelcomePanel onPick={(prompt) => void newThread(prompt)} />
                )}
                <DraftComposer onCreate={() => void newThread()} />
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
