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
  /**
   * Each stored turn's own citations, by turn id — which is the seeded assistant
   * message's id. Handing the newest verdict to every message made a stored answer
   * from an earlier turn render its markers as plain text, which is why only the
   * live answer had citation controls.
   */
  storedCitations: Record<string, AskCitation[]>;
  /**
   * Whether each stored turn ran a search, by turn id. A reuse turn made no
   * search, which is what tells "Reused earlier evidence" apart from "Partly
   * grounded" — and it must be read per turn, because a thread that answered a
   * question earlier still has passages on screen when a later "thanks" arrives.
   */
  storedSearched: Record<string, boolean>;
  /**
   * Whether the newest stored turn ran a search. Used when that turn has no
   * assistant message yet — a reload mid-answer seeds the question and nothing
   * else — so the badge describes the turn rather than the absence of a message.
   */
  newestStoredSearched: boolean | null;
  /**
   * A non-terminal note about the step in flight, e.g. `reasoning`. It is what
   * turns a silent model into "Thinking…" rather than a page that looks hung, and
   * it is cleared by any terminal frame (D-12).
   */
  status_note: string | null;
  /** The newest stored turn's status, so a stopped turn's badge can say so. */
  newestStoredStatus: AskTurn["status"] | null;
  /** The newest stored turn's answer text, so "stopped before answering" is decidable. */
  newestStoredAnswer: string;
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
      void askChatService.importSessions(sessions).catch(() => {
        // Only say so when there was something to carry over. A failure with
        // nothing to import is a no-op, and a red sentence for it would frighten a
        // member about a history that does not exist.
        onError("Your earlier conversations could not be carried over.");
      });
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
  // A new thread has no title until its first question; the server derives one and
  // this puts it in the list without a full reload.
  const onTitle = useCallback((threadId: string, title: string) => {
    if (!title) return;
    setThreads((current) =>
      current.map((item) => (item.thread_id === threadId ? { ...item, title } : item)),
    );
  }, []);
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
        states.current.set(
          threadId,
          threadState(threadId, detail.turns, states, forceRender, onTitle),
        );
        // The detail carries the authoritative title, which the list may not have
        // if the thread was named after the list was read.
        onTitle(threadId, detail.thread.title);
        forceRender((value) => value + 1);
      } catch (error) {
        onError(error instanceof Error ? error.message : "Unable to open that conversation.");
      } finally {
        setOpening(false);
      }
    },
    [onError, onTitle],
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
        threadState(created.thread_id, [], states, forceRender, onTitle),
      );
      forceRender((value) => value + 1);
      return created.thread_id;
    } catch (error) {
      onError(error instanceof Error ? error.message : "Unable to start a conversation.");
      return null;
    }
  }, [onError, onTitle]);

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

/**
 * Re-read a thread and put its stored answers back into the live message list.
 *
 * Called after a turn ends *or* is stopped. A stop aborts the stream mid-part,
 * which leaves the assistant message's text part in `state: "streaming"` — and
 * Assistant UI does not draw an unfinished part, so the answer the server had
 * already stored vanished from the screen along with its sources. The store is the
 * truth here, and re-seeding it from there makes Stop keep what the member read
 * (D-4, FR-014).
 */
function mergeStoredAnswers(messages: UIMessage[], turns: AskTurn[]): UIMessage[] {
  const stored = new Map(
    turns.filter((turn) => turn.answer).map((turn) => [turn.turn_id, turn.answer as string]),
  );
  return messages.map((message) => {
    if (message.role !== "assistant") return message;
    const text = stored.get(message.id);
    if (!text) return message;
    // A plain text part, with no `state`, is what a finished answer looks like —
    // and a part left `streaming` by an abort is what Assistant UI declines to draw.
    // Everything else the stream carried is kept: the tool parts are the sources
    // panel's input, so dropping them would answer the question without its
    // evidence (FR-007).
    const other = message.parts.filter((part) => part.type !== "text");
    return { ...message, parts: [{ type: "text" as const, text }, ...other] };
  });
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
  // Whether the turn the badge is describing ran a search. A stored turn records
  // it as its `retrieval_query`; a live one is still in the message list, so its
  // own tool parts answer the same question. The thread's passages cannot: they
  // survive from an earlier answer, and a "thanks" after one still shows them.
  //
  // A turn with no answer yet has no assistant message, which is the reload-mid-
  // answer case: the question is seeded and the text is still empty. Falling back
  // to the newest *stored* turn keeps the badge honest there, instead of reading
  // "no search" from a missing message and calling a searched turn a reuse.
  const newestAnswer = [...helpers.messages]
    .reverse()
    .find((message) => message.role === "assistant");
  const newestSearched = newestAnswer
    ? (state.storedSearched[newestAnswer.id] ??
      newestAnswer.parts.some((part) => part.type === "tool-search_documents"))
    : (state.newestStoredSearched ?? false);
  // The newest turn's status and text, so the badge can tell a stopped turn from a
  // deficient one. A live turn's status arrives on the terminal frame; a stored
  // one is on the message list's own text.
  const newestStatus = state.status ?? state.newestStoredStatus;
  const newestText = newestAnswer
    ? newestAnswer.parts.map((part) => (part.type === "text" ? part.text : "")).join("")
    : (state.newestStoredAnswer ?? "");

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
              // A grounded answer with no citations is "Partly grounded" when it
              // searched and "Reused earlier evidence" when it did not. The test is
              // the newest turn's own search, not whether the panel has passages.
              searched={newestSearched}
              status={newestStatus ?? undefined}
              answer={newestText}
            />
          ) : null}
          {state.status_note === "reasoning" ? (
            // Unobtrusive: a quiet line, not a spinner or a banner. It exists so a
            // long silent step reads as work in progress rather than a hung page,
            // and it disappears the moment the first word arrives (D-12).
            <span
              role="status"
              data-testid="ask-chat-thinking"
              className="text-label-sm text-muted-foreground"
            >
              Thinking…
            </span>
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
                  {() => <MessageRow state={state} onSelectCitation={selectCitation} />}
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
                runningTurnId={() => {
                  // A turn's assistant message takes its turn id, so the running
                  // turn is the newest assistant message's id — the same value a
                  // resume uses (FR-016).
                  const assistant = [...helpers.messages]
                    .reverse()
                    .find((message) => message.role === "assistant");
                  return assistant?.id ?? state.session.resumeTurnId.current;
                }}
              />
            </div>
          </div>
        </div>
        {showSources && sources.passages.length > 0 ? (
          // Stacked under the conversation until there is genuinely room beside
          // it, then a bounded column. `lg:max-w-sm` on the wrapper while the
          // panel asked for `26rem` is what made the panel overflow its own box:
          // the cards clipped their score and the panel grew a horizontal
          // scrollbar. One width, `min-w-0`, and no `shrink-0` on the child keep
          // the two in agreement, and stacking is what leaves the composer usable
          // at a narrow width (better-layout: hold structure until it breaks).
          <div className="max-h-[45%] w-full overflow-y-auto overflow-x-hidden border-t border-border/60 p-3 lg:max-h-none lg:w-96 lg:shrink-0 lg:border-s lg:border-t-0 xl:w-[26rem]">
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
  state,
  onSelectCitation,
}: {
  state: ThreadState;
  onSelectCitation: (rank: number) => void;
}) {
  const role = useAuiState((value) => value.message.role);
  const id = useAuiState((value) => value.message.id);
  const isLast = useAuiState((value) => value.message.isLast);
  // A stored answer carries the citations its own turn recorded; the newest live
  // one takes the verdict the stream reported. Falling back to the newest verdict
  // for every message is what left an older answer's markers as plain text.
  const citations = state.storedCitations[id] ?? (isLast ? (state.verdict?.citations ?? []) : []);
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
  runningTurnId,
}: {
  threadId: string;
  canRegenerate: boolean;
  isRefusal: boolean;
  onRegenerate: () => void;
  /** The turn being written, if one is, so Stop can name it (FR-014). */
  runningTurnId: () => string | null;
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
            {/* Stop is two things now: end the local stream, and tell the server
                to stop the turn. The server cannot infer it from the socket —
                that is also what a reload does, and a reload must let the answer
                finish (FR-016) — so the intent has to be said out loud. */}
            <ComposerPrimitive.Cancel asChild>
              <button
                type="button"
                aria-label="Stop generating"
                className={COMPOSER_BUTTON}
                onClick={() => {
                  const turnId = runningTurnId();
                  if (turnId) void askChatService.cancelTurn(turnId).catch(() => undefined);
                }}
              >
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
  onTitle?: (threadId: string, title: string) => void,
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
      onStatus: (status) => {
        const entry = states.current.get(threadId);
        if (!entry) return;
        // A status is about the step in flight, so it is cleared on a terminal
        // frame; storing it here is what lets the page say "Thinking…" instead of
        // showing an empty bubble while the model reasons (D-12).
        entry.status_note = status;
        forceRender((value) => value + 1);
      },
      onTurn: (frame) => {
        const entry = states.current.get(threadId);
        if (!entry) return;
        entry.status = frame.status;
        entry.status_note = null;
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
        // stream ends. It also refreshes the stored answer text, its verdict, and —
        // for a thread's first question — the title the server derived from it.
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
            entry.storedCitations = Object.fromEntries(
              detail.turns.map((turn) => [turn.turn_id, turn.citations]),
            );
            entry.storedSearched = searchedByTurn(detail.turns);
            entry.newestStoredSearched = newestSearchedOf(detail.turns);
            const newestTurn = detail.turns[detail.turns.length - 1];
            entry.newestStoredStatus = newestTurn?.status ?? null;
            entry.newestStoredAnswer = newestTurn?.answer ?? "";
            // The step is over, so its status note is stale whatever it said.
            entry.status_note = null;
            // The store's passages for the turn that just ended, so a stopped
            // stream's sources panel survives the abort: the tool parts are gone
            // with the unfinished message, and these are the same hits (FR-007).
            const storedPassages = [...detail.turns]
              .reverse()
              .find((turn) => turn.passages?.length)?.passages;
            if (storedPassages?.length) entry.storedPassages = storedPassages;
            // A stopped stream leaves the answer's text part unfinished, which the
            // runtime will not draw. The store already holds it, so the message is
            // rewritten from there and Stop keeps what the member read.
            entry.session.chat.messages = mergeStoredAnswers(
              entry.session.chat.messages,
              detail.turns,
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
            onTitle?.(threadId, detail.thread.title);
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
    storedCitations: Object.fromEntries(turns.map((turn) => [turn.turn_id, turn.citations])),
    storedSearched: searchedByTurn(turns),
    newestStoredSearched: newestSearchedOf(turns),
    status_note: null,
    newestStoredStatus: turns[turns.length - 1]?.status ?? null,
    newestStoredAnswer: turns[turns.length - 1]?.answer ?? "",
  };
}

/**
 * Which stored turns ran a search, by turn id.
 *
 * A turn that searches records the standalone query it searched for, so
 * `retrieval_query` is the store's own answer to "did this turn look anything up".
 * Its passages corroborate it: passages exist only because a search returned them.
 * A reuse turn — a "thanks", a "make that shorter" — has neither, and that is
 * exactly the turn whose badge must not claim a search happened.
 */
function searchedByTurn(turns: AskTurn[]): Record<string, boolean> {
  return Object.fromEntries(
    turns.map((turn) => [
      turn.turn_id,
      Boolean(turn.retrieval_query) || (turn.passages?.length ?? 0) > 0,
    ]),
  );
}

/** Whether the newest stored turn searched, for a turn with no answer yet. */
function newestSearchedOf(turns: AskTurn[]): boolean | null {
  const newest = turns[turns.length - 1];
  if (!newest) return null;
  return Boolean(newest.retrieval_query) || (newest.passages?.length ?? 0) > 0;
}

/**
 * The verdict the store holds for the newest turn, if that turn has one.
 *
 * It must be the **newest** turn's verdict and nothing else. Scanning back to
 * the last turn that had a verdict made a running turn — which has none yet —
 * inherit the previous answer's badge, so a reload mid-answer showed a verdict
 * about an answer that was not on screen, and the wrong one.
 */
function verdictFromTurns(turns: AskTurn[]): AskVerdict | null {
  const newest = turns[turns.length - 1];
  if (!newest?.grounding) return null;
  return { grounding: newest.grounding, citations: newest.citations, maxScore: 0, cutoff: 0 };
}
