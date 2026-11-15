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
import { GroundingBadge } from "@/features/chat/answer-grounding";
import {
  AnswerWithCitations,
  SourcesPanel,
  SourcesToggle,
  type SourcesState,
} from "@/features/chat/sources-panel";
import { IndexPreparing } from "@/features/index/index-preparing";
import { cn } from "@/lib/utils";
import {
  askSearchOutput,
  askTurnStatus,
  type AskCitation,
  type AskGrounding,
  type AskGroundingFrame,
  type AskPassage,
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
  /** Stored turns' interaction ids, by turn id, so a passage vote has a target. */
  storedAnswers: Record<string, string>;
  /** The newest stored turn's passages, read from the thread response on load. */
  storedPassages: AskPassage[];
};

const COMPOSER_BUTTON =
  "flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground transition-all hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

const COMPOSER_INPUT =
  "max-h-48 min-h-10 w-full flex-1 resize-none bg-transparent px-2 py-2 text-base outline-none placeholder:text-muted-foreground/70 md:text-sm";

export const ASK_DISCLAIMER =
  "Answers cite the passages they came from. Press Stop to end a reply you do not need.";

/** The retired page's storage keys, and the import's one-time marker. */
export function legacyStorageKeys(ownerId: string): {
  sessions: string;
  active: string;
  imported: string;
} {
  return {
    sessions: `deepfix.chat_sessions.v2:${ownerId}`,
    active: `deepfix.active_session_id.v2:${ownerId}`,
    imported: `deepfix.chat_imported.v2:${ownerId}`,
  };
}

type LegacySession = {
  id: string;
  title: string;
  createdAt: number | string;
  updatedAt: number | string;
  turns: { interactionId?: string | null; userQuery: string; answer?: string | null }[];
};

/**
 * Import the browser's old conversations once, then stop looking (FR-033).
 *
 * The keys are removed whatever happens: a member left on a store the server
 * cannot see would keep two copies of the same history that silently diverge, and
 * a retry loop against a store the server cannot read cannot converge. A failure
 * is reported once, in a sentence, rather than repeated.
 */
export function useLegacyImport(ownerId: string | null, onError: (message: string) => void): void {
  const attempted = useRef(false);
  useEffect(() => {
    if (!ownerId || attempted.current) return;
    attempted.current = true;
    const keys = legacyStorageKeys(ownerId);
    let raw: string | null = null;
    try {
      if (window.localStorage.getItem(keys.imported)) {
        // An earlier visit already imported and cleaned up; nothing to send.
        window.localStorage.removeItem(keys.active);
        window.localStorage.removeItem(keys.sessions);
        return;
      }
      raw = window.localStorage.getItem(keys.sessions);
    } catch {
      return;
    }
    const sessions = parseLegacySessions(raw);
    if (sessions.length > 0) {
      void askChatService
        .importSessions(sessions)
        .catch(() => onError("Your earlier conversations could not be carried over."));
    }
    try {
      window.localStorage.removeItem(keys.sessions);
      window.localStorage.removeItem(keys.active);
      window.localStorage.setItem(keys.imported, "1");
    } catch {
      // A browser with storage disabled has nothing to clean up.
    }
  }, [ownerId, onError]);
}

/** The stored sessions, or an empty list when the key is absent or unreadable. */
export function parseLegacySessions(raw: string | null): LegacySession[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const session = item as Record<string, unknown>;
      if (typeof session["id"] !== "string" || !session["id"]) return [];
      const turns = Array.isArray(session["turns"]) ? session["turns"] : [];
      return [
        {
          id: session["id"],
          title: typeof session["title"] === "string" ? session["title"] : "",
          createdAt: (session["createdAt"] as number | string) ?? Date.now(),
          updatedAt: (session["updatedAt"] as number | string) ?? Date.now(),
          turns: turns.flatMap((turn) => {
            if (!turn || typeof turn !== "object") return [];
            const value = turn as Record<string, unknown>;
            const query = typeof value["userQuery"] === "string" ? value["userQuery"] : "";
            const interactionId =
              typeof value["interactionId"] === "string" ? value["interactionId"] : null;
            // A turn with no interaction cannot be linked, so it is not sent: the
            // server would only skip it, and the count is not the member's news.
            if (!query || !interactionId) return [];
            return [
              {
                interactionId,
                userQuery: query,
                answer: typeof value["answer"] === "string" ? value["answer"] : null,
              },
            ];
          }),
        },
      ];
    });
  } catch {
    return [];
  }
}

export function AskPage({
  indexReady = true,
  ownerId = null,
}: {
  indexReady?: boolean;
  /** The signed-in member, needed only to find their legacy browser sessions. */
  ownerId?: string | null;
}) {
  const [threads, setThreads] = useState<{ thread_id: string; title: string }[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);
  const states = useRef(new Map<string, ThreadState>());
  const [, forceRender] = useState(0);

  const onError = useCallback((message: string) => setListError(message), []);
  useLegacyImport(ownerId, onError);

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
  const [showSources, setShowSources] = useState(true);
  const [selectedRank, setSelectedRank] = useState<number | null>(null);
  // The passages come from the messages' tool parts, so a reload rebuilds the
  // panel from what the stream carried rather than from the answer's prose.
  const sources: SourcesState = {
    // The streamed tool output wins while a turn is live; a reloaded thread has
    // no such part, so it falls back to the passages the store returned with the
    // turn (FR-007, AC 3).
    passages:
      passagesFromMessages(helpers.messages).length > 0
        ? passagesFromMessages(helpers.messages)
        : state.storedPassages,
    citations: state.verdict?.citations ?? [],
    interactionId: latestInteractionId(state, helpers.messages),
  };
  const selectCitation = (rank: number) => {
    setShowSources(true);
    setSelectedRank(rank);
  };

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
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-border/60 bg-muted/20 px-4">
        <span className="truncate text-label-sm font-medium text-foreground">Conversation</span>
        <div className="flex items-center gap-2">
          {state.verdict ? (
            <GroundingBadge
              grounding={state.verdict.grounding}
              citations={state.verdict.citations}
            />
          ) : null}
          {sources.passages.length > 0 ? (
            <SourcesToggle
              open={showSources}
              count={sources.passages.length}
              onToggle={() => setShowSources((open) => !open)}
            />
          ) : null}
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden lg:flex-row">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
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
                <ThreadPrimitive.Messages>
                  {() => (
                    <MessageRow
                      citations={latestCitations(state)}
                      onSelectCitation={selectCitation}
                    />
                  )}
                </ThreadPrimitive.Messages>
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
        </div>
        {showSources && sources.passages.length > 0 ? (
          <div className="w-full shrink-0 overflow-y-auto border-s border-border/60 p-3 md:w-auto lg:max-w-sm">
            <SourcesPanel
              state={sources}
              selectedRank={selectedRank}
              onSelect={setSelectedRank}
              onVote={() => undefined}
            />
          </div>
        ) : null}
      </div>
    </AssistantRuntimeProvider>
  );
}

function MessageRow({
  citations,
  onSelectCitation,
}: {
  citations: AskCitation[];
  onSelectCitation: (rank: number) => void;
}) {
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
                <AnswerWithCitations
                  answer={text}
                  citations={citations}
                  onSelect={onSelectCitation}
                />
              ),
          }}
        />
      </MessagePrimitive.Root>
    </div>
  );
}

/**
 * Every passage the conversation's tool calls delivered, in the order they
 * arrived, one entry per rank of the newest search.
 *
 * `search_documents` is the only tool, so a `tool-search_documents` part with an
 * output is the panel's source. Later calls win: a turn that searched twice is
 * showing the passages its answer was built from.
 */
export function passagesFromMessages(messages: UIMessage[]): AskPassage[] {
  let latest: AskPassage[] = [];
  for (const message of messages) {
    for (const part of message.parts) {
      if (part.type !== "tool-search_documents") continue;
      const output = (part as { output?: unknown }).output;
      const parsed = askSearchOutput.safeParse(output);
      if (parsed.success) latest = parsed.data.passages;
    }
  }
  return latest;
}

/** The newest verdict's citations, for the answer currently on screen. */
function latestCitations(state: ThreadState): AskCitation[] {
  return state.verdict?.citations ?? [];
}

/**
 * The interaction the newest answer belongs to, which is what a vote names.
 *
 * A live turn has no id until the server has stored its row, so this reads the
 * stored map the `onFinished` refresh fills in. `null` means the vote falls back
 * to local state, which is what the passage card does when there is nothing to
 * POST to yet.
 */
function latestInteractionId(state: ThreadState, messages: UIMessage[]): string | null {
  const assistant = [...messages].reverse().find((message) => message.role === "assistant");
  if (!assistant) return null;
  return state.storedAnswers[assistant.id] ?? null;
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
      onFinished: () => {
        // A stored turn's interaction id is what a vote needs, and it only exists
        // once the server has written the row, so the thread is re-read after the
        // stream ends. It also refreshes the stored answer text and its verdict.
        void askChatService
          .getThread(threadId)
          .then((detail) => {
            const entry = states.current.get(threadId);
            if (!entry) return;
            entry.storedAnswers = Object.fromEntries(
              detail.turns
                .filter((turn) => turn.interaction_id)
                .map((turn) => [turn.turn_id, turn.interaction_id as string]),
            );
            const newest = detail.turns[detail.turns.length - 1];
            if (newest?.grounding) {
              entry.verdict = {
                grounding: newest.grounding,
                citations: newest.citations,
                maxScore: entry.verdict?.maxScore ?? 0,
                cutoff: entry.verdict?.cutoff ?? 0,
              };
            }
            forceRender((value) => value + 1);
          })
          .catch(() => undefined);
      },
    },
  };
  const session = createAskChat(threadId, seedMessages(turns), hooks);
  // A turn the store still calls `running` is resumed on mount, so a reload
  // mid-answer shows the stored text and then the rest of the reply.
  const running = turns.find((turn) => turn.status === "running");
  if (running) session.resumeTurnId.current = running.turn_id;
  return {
    session,
    verdict: verdictFromTurns(turns),
    status: null,
    error: null,
    storedAnswers: Object.fromEntries(
      turns
        .filter((turn) => turn.interaction_id)
        .map((turn) => [turn.turn_id, turn.interaction_id as string]),
    ),
    storedPassages: [...turns].reverse().find((turn) => turn.passages?.length)?.passages ?? [],
  };
}

/** The verdict the store already holds for the newest answered turn, if any. */
function verdictFromTurns(turns: AskTurn[]): AskVerdict | null {
  const answered = [...turns]
    .reverse()
    .find((turn) => askTurnStatus.safeParse(turn.status).success && turn.grounding);
  if (!answered?.grounding) return null;
  return { grounding: answered.grounding, citations: answered.citations, maxScore: 0, cutoff: 0 };
}
