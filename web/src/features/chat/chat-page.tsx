import type { UIMessage } from "ai";
import { useChat } from "@ai-sdk/react";
import {
  AssistantRuntimeProvider,
  AuiIf,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
} from "@assistant-ui/react";
import { useAISDKRuntime } from "@assistant-ui/react-ai-sdk";
import { ArrowUp, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ChatComposerBar, ChatComposerHint } from "@/components/chat/composer";
import { ChatMarkdown } from "@/components/chat/markdown";
import {
  createAskChat,
  seedMessages,
  type AskChatHooks,
  type AskThreadChat,
} from "@/features/chat/ask-chat-transport";
import { AskThreadSidebar } from "@/features/chat/ask-thread-sidebar";
import { IndexPreparing } from "@/features/index/index-preparing";
import { cn } from "@/lib/utils";
import {
  askTurnStatus,
  type AskGrounding,
  type AskGroundingFrame,
  type AskTurn,
} from "@/schemas/ask-chat";
import { askChatService } from "@/services/ask-chat-service";

/**
 * The Ask conversation page.
 *
 * Assistant UI drives the message column on the Vercel AI SDK runtime, with one
 * `Chat` per thread (`ask-chat-transport.ts`). One runtime per thread is what
 * keeps an answer streaming here while the member reads another conversation:
 * the `Chat` owns its messages and its stream, so a reply cannot appear in a
 * thread that did not ask for it.
 *
 * The page holds no protocol knowledge. Threads come from `askChatService`, the
 * stream arrives through the transport, and the two frames this page needs — the
 * grounding verdict and the terminal status — reach it through the transport's
 * hooks rather than being parsed here.
 *
 * Regenerate is the runtime's own reload, which the server resolves to the newest
 * answered turn. The page never names a turn id, because the AI SDK's reload
 * drops the message it is replacing.
 */

/** What a finished answer claims about its evidence. */
export type AskVerdict = {
  grounding: AskGrounding;
  citations: AskGroundingFrame["citations"];
  maxScore: number;
  cutoff: number;
};

type ThreadState = {
  session: AskThreadChat;
  verdict: AskVerdict | null;
  status: AskTurn["status"] | null;
  error: string | null;
};

const COMPOSER_BUTTON =
  "flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground transition-all hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

const COMPOSER_INPUT =
  "max-h-48 min-h-10 w-full flex-1 resize-none bg-transparent px-2 py-2 text-base outline-none placeholder:text-muted-foreground/70 md:text-sm";

export const ASK_DISCLAIMER =
  "Answers cite the passages they came from. Press Stop to end a reply you do not need.";

export function AskPage({ indexReady = true }: { indexReady?: boolean }) {
  const [threads, setThreads] = useState<{ thread_id: string; title: string }[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);
  const states = useRef(new Map<string, ThreadState>());
  const [, forceRender] = useState(0);

  const onError = useCallback((message: string) => setListError(message), []);

  const refresh = useCallback(async () => {
    try {
      const listed = await askChatService.listThreads();
      setThreads(listed.map((item) => ({ thread_id: item.thread_id, title: item.title })));
      setListError(null);
      return listed;
    } catch (error) {
      onError(error instanceof Error ? error.message : "Unable to load your conversations.");
      return [];
    }
  }, [onError]);

  const openThread = useCallback(
    async (threadId: string) => {
      setActiveThreadId(threadId);
      if (states.current.has(threadId)) return;
      setOpening(true);
      try {
        const detail = await askChatService.getThread(threadId);
        states.current.set(threadId, threadState(threadId, detail.turns, states, forceRender));
        forceRender((value) => value + 1);
      } catch (error) {
        onError(error instanceof Error ? error.message : "Unable to open that conversation.");
      } finally {
        setOpening(false);
      }
    },
    [onError],
  );

  // The newest thread opens on first load; a member with none sees the empty state.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const listed = await refresh();
      const newest = listed[0]?.thread_id;
      if (!cancelled && newest) void openThread(newest);
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh, openThread]);

  const createThread = useCallback(async (): Promise<string | null> => {
    try {
      const created = await askChatService.createThread();
      setThreads((current) => [{ thread_id: created.thread_id, title: created.title }, ...current]);
      setActiveThreadId(created.thread_id);
      states.current.set(
        created.thread_id,
        threadState(created.thread_id, [], states, forceRender),
      );
      forceRender((value) => value + 1);
      return created.thread_id;
    } catch (error) {
      onError(error instanceof Error ? error.message : "Unable to start a conversation.");
      return null;
    }
  }, [onError]);

  const renameThread = useCallback(
    async (threadId: string, title: string) => {
      try {
        const renamed = await askChatService.renameThread(threadId, title);
        setThreads((current) =>
          current.map((item) =>
            item.thread_id === threadId ? { ...item, title: renamed.title } : item,
          ),
        );
      } catch (error) {
        onError(error instanceof Error ? error.message : "Unable to rename that conversation.");
      }
    },
    [onError],
  );

  const deleteThread = useCallback(
    async (threadId: string) => {
      try {
        await askChatService.deleteThread(threadId);
      } catch (error) {
        onError(error instanceof Error ? error.message : "Unable to delete that conversation.");
        return;
      }
      // A deleted thread stops streaming: its reply has nowhere to land.
      void states.current.get(threadId)?.session.chat.stop();
      states.current.delete(threadId);
      setThreads((current) => {
        const remaining = current.filter((item) => item.thread_id !== threadId);
        setActiveThreadId((active) =>
          active === threadId ? (remaining[0]?.thread_id ?? null) : active,
        );
        return remaining;
      });
    },
    [onError],
  );

  if (!indexReady) {
    return (
      <div className="mx-auto max-w-3xl px-8 py-10">
        <IndexPreparing />
      </div>
    );
  }

  const active = activeThreadId ? states.current.get(activeThreadId) : null;

  return (
    <div className="mx-auto flex h-[calc(100vh-4.25rem)] w-full max-w-[96rem] flex-1 overflow-hidden px-2 py-3 sm:px-4 lg:px-6">
      <div className="relative flex flex-1 gap-4 overflow-hidden">
        <AskThreadSidebar
          threads={threads}
          activeThreadId={activeThreadId}
          error={listError}
          onCreate={() => void createThread()}
          onOpen={(threadId) => void openThread(threadId)}
          onRename={renameThread}
          onDelete={deleteThread}
        />
        <section className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-border/80 bg-background/50 shadow-sm backdrop-blur-sm">
          {active && activeThreadId ? (
            <>
              {opening ? (
                <p role="status" className="px-4 py-2 text-label-sm text-muted-foreground">
                  Opening the conversation…
                </p>
              ) : null}
              <ThreadSurface key={activeThreadId} threadId={activeThreadId} state={active} />
            </>
          ) : (
            <EmptyThread onCreate={() => void createThread()} />
          )}
        </section>
      </div>
    </div>
  );
}

/** One thread's runtime, message column, and composer. */
function ThreadSurface({ threadId, state }: { threadId: string; state: ThreadState }) {
  const helpers = useChat<UIMessage>({ chat: state.session.chat });
  const runtime = useAISDKRuntime(helpers);

  useEffect(() => {
    if (!state.session.resumeTurnId.current) return;
    // The stored text is already seeded; the reconnect replays it from the
    // turn's own id, so it replaces that message rather than duplicating it.
    void helpers.resumeStream().catch(() => undefined);
    // Resume once per stored turn: `helpers` is a fresh object every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="flex h-12 shrink-0 items-center border-b border-border/60 bg-muted/20 px-4">
        <span className="truncate text-label-sm font-medium text-foreground">Conversation</span>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-6">
        <div
          aria-live="polite"
          data-testid="ask-chat-stream"
          className="mx-auto flex max-w-3xl flex-col gap-6"
        >
          <ThreadPrimitive.Root className="flex flex-col gap-6">
            <ThreadPrimitive.Empty>
              <div className="rounded-3xl border border-dashed border-border/80 bg-gradient-to-b from-muted/30 via-muted/15 to-transparent px-6 py-14 text-center">
                <h2 className="text-headline-md text-foreground">Ask about your corpus</h2>
                <p className="mt-2 text-body-md text-muted-foreground">
                  Every answer cites the passages it used, and every passage stays votable.
                </p>
              </div>
            </ThreadPrimitive.Empty>
            <ThreadPrimitive.Messages>{() => <MessageRow />}</ThreadPrimitive.Messages>
            <ThreadError state={state} />
          </ThreadPrimitive.Root>
        </div>
      </div>
      <div className="border-t border-border/60 bg-background/95 p-3 sm:p-4">
        <div className="mx-auto max-w-3xl">
          <AskComposer
            threadId={threadId}
            onRegenerate={() => void helpers.regenerate()}
            canRegenerate={state.status === "completed" || state.status === "failed"}
            isRefusal={state.verdict?.grounding === "no_relevant_sources"}
          />
        </div>
      </div>
    </AssistantRuntimeProvider>
  );
}

function MessageRow() {
  const role = useAuiState((state) => state.message.role);
  return (
    <div data-role={role}>
      <MessagePrimitive.Root>
        <MessagePrimitive.Parts
          components={{
            Text: ({ text }) =>
              role === "user" ? (
                <p className="ms-auto max-w-[85%] whitespace-pre-wrap rounded-2xl bg-primary px-4 py-3 text-body-md text-primary-foreground">
                  {text}
                </p>
              ) : (
                <div className="text-body-md leading-relaxed text-foreground">
                  <ChatMarkdown prose={false}>{text}</ChatMarkdown>
                </div>
              ),
          }}
        />
      </MessagePrimitive.Root>
    </div>
  );
}

function AskComposer({
  threadId,
  canRegenerate,
  isRefusal,
  onRegenerate,
}: {
  threadId: string;
  canRegenerate: boolean;
  isRefusal: boolean;
  onRegenerate: () => void;
}) {
  return (
    <div className="flex flex-col">
      <ComposerPrimitive.Root className="flex flex-col">
        <ChatComposerBar className="items-end">
          <ComposerPrimitive.Input
            aria-label="Ask"
            placeholder="Ask a question about the documents…"
            rows={1}
            className={cn(COMPOSER_INPUT)}
          />
          <AuiIf condition={(state) => !state.thread.isRunning}>
            <ComposerPrimitive.Send asChild>
              <button type="submit" aria-label="Ask" className={COMPOSER_BUTTON}>
                <ArrowUp className="size-4" aria-hidden />
              </button>
            </ComposerPrimitive.Send>
          </AuiIf>
          <AuiIf condition={(state) => state.thread.isRunning}>
            <ComposerPrimitive.Cancel asChild>
              <button type="button" aria-label="Stop generating" className={COMPOSER_BUTTON}>
                <Square className="size-3.5 fill-current" aria-hidden />
              </button>
            </ComposerPrimitive.Cancel>
          </AuiIf>
        </ChatComposerBar>
      </ComposerPrimitive.Root>
      <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2">
        <ChatComposerHint className="mt-0 text-left">{ASK_DISCLAIMER}</ChatComposerHint>
        {/* A refusal is the answer, so regenerate is not offered for it (D-3). */}
        {canRegenerate && !isRefusal ? (
          <button
            type="button"
            onClick={onRegenerate}
            className="text-label-sm text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            data-thread={threadId}
          >
            Regenerate
          </button>
        ) : null}
      </div>
    </div>
  );
}

function EmptyThread({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
      <h2 className="text-headline-md text-foreground">No conversations yet</h2>
      <p className="max-w-sm text-body-md text-muted-foreground">
        Start one to ask about the documents in this corpus. Your conversations stay on the server,
        so they are here when you come back.
      </p>
      <button
        type="button"
        onClick={onCreate}
        className="mt-2 rounded-xl bg-primary px-4 py-2 text-label-md font-medium text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Start a conversation
      </button>
    </div>
  );
}

function ThreadError({ state }: { state: ThreadState }) {
  const isRunning = useAuiState((value) => value.thread.isRunning);
  if (!state.error || isRunning) return null;
  return (
    <p role="alert" className="text-label-sm text-destructive">
      {state.error}
    </p>
  );
}

/** Build a thread's session and the hooks that report its frames back to the page. */
function threadState(
  threadId: string,
  turns: AskTurn[],
  states: { current: Map<string, ThreadState> },
  forceRender: (update: (value: number) => number) => void,
): ThreadState {
  const hooks: { current: AskChatHooks } = {
    current: {
      onGrounding: (frame) => {
        const entry = states.current.get(threadId);
        if (!entry) return;
        entry.verdict = {
          grounding: frame.grounding,
          citations: frame.citations,
          maxScore: frame.max_score,
          cutoff: frame.cutoff,
        };
        forceRender((value) => value + 1);
      },
      onTurn: (frame) => {
        const entry = states.current.get(threadId);
        if (!entry) return;
        entry.status = frame.status;
        if (frame.grounding) {
          entry.verdict = {
            grounding: frame.grounding,
            citations: frame.citations ?? [],
            maxScore: entry.verdict?.maxScore ?? 0,
            cutoff: entry.verdict?.cutoff ?? 0,
          };
        }
        forceRender((value) => value + 1);
      },
      onError: (message) => {
        const entry = states.current.get(threadId);
        if (!entry) return;
        entry.error = message;
        forceRender((value) => value + 1);
      },
      onFinished: () => forceRender((value) => value + 1),
    },
  };
  const session = createAskChat(threadId, seedMessages(turns), hooks);
  // A turn the store still calls `running` is resumed on mount, so a reload
  // mid-answer shows the stored text and then the rest of the reply.
  const running = turns.find((turn) => turn.status === "running");
  if (running) session.resumeTurnId.current = running.turn_id;
  return { session, verdict: verdictFromTurns(turns), status: null, error: null };
}

/** The verdict the store already holds for the newest answered turn, if any. */
function verdictFromTurns(turns: AskTurn[]): AskVerdict | null {
  const answered = [...turns]
    .reverse()
    .find((turn) => askTurnStatus.safeParse(turn.status).success && turn.grounding);
  if (!answered?.grounding) return null;
  return { grounding: answered.grounding, citations: answered.citations, maxScore: 0, cutoff: 0 };
}
