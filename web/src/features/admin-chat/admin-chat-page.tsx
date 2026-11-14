import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { AssistantChatTransport, useChatRuntime } from "@assistant-ui/react-ai-sdk";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/layout/app-shell";
import { ChatComposer } from "@/features/admin-chat/chat-composer";
import { ActionCard } from "@/features/admin-chat/action-card";
import { ChatMessageView } from "@/features/admin-chat/chat-message";
import { RunPanel } from "@/features/admin-chat/run-panel";
import type { ChatActionCard, ChatRun, ChatThreadSummary } from "@/schemas/admin-chat";
import { adminChatService } from "@/services/admin-chat-service";

/**
 * The admin chat page.
 *
 * The assistant half is Assistant UI on the Vercel AI SDK's runtime: one
 * `useChatRuntime()` from `@assistant-ui/react-ai-sdk`, with a transport aimed
 * at the same-origin `/api/admin/chat/completions` route the web server already
 * proxies to FastAPI. There is no `useExternalStoreRuntime` and no hand-rolled
 * stream parser in this component (PRD D-7); the JSON-free parts the runtime
 * does not type (a tool-progress row, the terminal turn and its action cards)
 * are read by the transport in `admin-chat-sse.ts`.
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

interface TurnState {
  /** The assistant text streamed for the active turn, in order. */
  text: string;
  /** Tool-progress rows, newest last. */
  tools: string[];
  /** The terminal frame, once the turn ends. */
  turn: { turn_id: string; status: string } | null;
  error: string | null;
}

const EMPTY_TURN: TurnState = { text: "", tools: [], turn: null, error: null };

function AdminChat() {
  const [threads, setThreads] = useState<ChatThreadSummary[]>([]);
  const [activeThread, setActiveThread] = useState<string | null>(null);
  const [messages, setMessages] = useState<{ role: "user" | "assistant"; content: string }[]>([]);
  const [actions, setActions] = useState<ChatActionCard[]>([]);
  const [runs, setRuns] = useState<ChatRun[]>([]);
  const [turn, setTurn] = useState<TurnState>(EMPTY_TURN);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const composer = useRef<HTMLTextAreaElement | null>(null);

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

  useEffect(() => {
    if (!activeThread) {
      setMessages([]);
      setActions([]);
      setRuns([]);
      return;
    }
    let cancelled = false;
    void adminChatService
      .thread(activeThread)
      .then((detail) => {
        if (cancelled) return;
        setMessages(detail.messages.map((m) => ({ role: m.role, content: m.content })));
        setActions(
          detail.actions.map(toCard).filter((card): card is ChatActionCard => card !== null),
        );
      })
      .catch((error: unknown) => setFailure(message(error)));
    void adminChatService
      .runs(activeThread)
      .then((items) => {
        if (!cancelled) setRuns(items);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [activeThread]);

  const send = useCallback(
    (content: string) => {
      if (!activeThread) return;
      const next = [...messages, { role: "user" as const, content }];
      setMessages(next);
      setTurn({ ...EMPTY_TURN, text: "" });
      setBusy(true);
      abort.current = new AbortController();
      void adminChatService
        .streamTurn(
          { threadId: activeThread, messages: next, signal: abort.current.signal },
          {
            onText: (delta) => setTurn((state) => ({ ...state, text: state.text + delta })),
            onTool: (progress) =>
              setTurn((state) => ({
                ...state,
                tools: [...state.tools, progress.label ?? progress.tool ?? "Working\u2026"],
              })),
            onTurn: (frame) => {
              setTurn((state) => ({
                ...state,
                turn: { turn_id: frame.turn_id, status: frame.status },
              }));
              setActions((current) => [...current, ...frame.actions]);
            },
            onError: (text) => setTurn((state) => ({ ...state, error: text })),
            onDone: () => {
              setBusy(false);
              void refreshThreads();
              void adminChatService
                .thread(activeThread)
                .then((detail) =>
                  setMessages(detail.messages.map((m) => ({ role: m.role, content: m.content }))),
                );
            },
          },
        )
        .finally(() => {
          setBusy(false);
          composer.current?.focus();
        });
    },
    [activeThread, messages, refreshThreads],
  );

  const stop = useCallback(() => {
    abort.current?.abort();
    setBusy(false);
  }, []);

  const newThread = useCallback(async () => {
    try {
      const created = await adminChatService.createThread();
      setActiveThread(created.thread_id);
      setMessages([]);
      setActions([]);
      setRuns([]);
      setTurn(EMPTY_TURN);
      await refreshThreads();
    } catch (error) {
      setFailure(message(error));
    }
  }, [refreshThreads]);

  const deleteThread = useCallback(
    async (threadId: string) => {
      try {
        await adminChatService.deleteThread(threadId);
        const items = await refreshThreads();
        setActiveThread(items[0]?.thread_id ?? null);
      } catch (error) {
        setFailure(message(error));
      }
    },
    [refreshThreads],
  );

  const confirm = useCallback(
    async (card: ChatActionCard) => {
      try {
        const run = await adminChatService.confirmAction(card.action_id);
        setActions((current) =>
          current.map((item) =>
            item.action_id === card.action_id ? { ...item, status: "confirmed" } : item,
          ),
        );
        if (activeThread) {
          await adminChatService.linkRun(activeThread, run.run_id);
          setRuns(await adminChatService.runs(activeThread));
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
      setActions((current) =>
        current.map((item) =>
          item.action_id === card.action_id ? { ...item, status: "rejected" } : item,
        ),
      );
    } catch (error) {
      setFailure(message(error));
    }
  }, []);

  const runtime = useChatRuntime({
    transport: useMemo(
      () =>
        new AssistantChatTransport({
          api: "/api/admin/chat/completions",
          credentials: "include",
          prepareSendMessagesRequest: ({ id }) => ({
            body: { thread_id: activeThread ?? id, messages: [] },
          }),
        }),
      [activeThread],
    ),
  });

  if (failure?.includes("not configured")) {
    return <AdminChatMisconfigured message={failure} />;
  }

  return (
    <AssistantRuntimeProvider runtime={runtime}>
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
          <div className="flex min-h-[32rem] flex-col gap-4">
            <div aria-live="polite" className="flex flex-col gap-3" data-testid="admin-chat-stream">
              {messages.map((item, index) => (
                <ChatMessageView
                  key={`${index}-${item.role}`}
                  role={item.role}
                  content={item.content}
                />
              ))}
              {turn.text ? <ChatMessageView role="assistant" content={turn.text} /> : null}
              {turn.tools.map((label, index) => (
                <p key={`${label}-${index}`} className="text-label-sm text-muted-foreground">
                  {label}
                </p>
              ))}
              {turn.error ? (
                <p role="alert" className="text-label-sm text-destructive">
                  {turn.error}
                </p>
              ) : null}
              {messages.length === 0 && !turn.text ? (
                <p className="text-body-md text-muted-foreground">
                  No messages yet. Ask a question to start.
                </p>
              ) : null}
            </div>
            {actions.length ? (
              <div className="flex flex-col gap-3">
                {actions.map((card) => (
                  <ActionCard
                    key={card.action_id}
                    card={card}
                    onConfirm={confirm}
                    onDismiss={dismiss}
                  />
                ))}
              </div>
            ) : null}
            {runs.map((run) => (
              <RunPanel key={run.run_id} run={run} />
            ))}
            <ChatComposer
              busy={busy}
              inputRef={composer}
              onSend={send}
              onStop={stop}
              disabled={!activeThread}
            />
            {failure ? (
              <p role="alert" className="text-label-sm text-destructive">
                {failure}
              </p>
            ) : null}
          </div>
        </div>
      </div>
    </AssistantRuntimeProvider>
  );
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
