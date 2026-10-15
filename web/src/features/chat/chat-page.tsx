import { useEffect, useState, type FormEvent } from "react";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  fromThreadMessageLike,
  ThreadPrimitive,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import {
  ArrowUp,
  BookOpen,
  Bot,
  CornerDownLeft,
  FileText,
  Loader2,
  PanelRightClose,
  PanelRightOpen,
  Sparkles,
  User as UserIcon,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { TopBar } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { VoteButtons, type Vote } from "@/features/feedback/vote-buttons";
import { IndexPreparing } from "@/features/index/index-preparing";
import { askService, type AskHit } from "@/services/ask-service";
import { feedbackService } from "@/services/feedback-service";

const ASK_INTERACTION = "deepfix.ask.interaction";

const COMPLETE_STATUS = { type: "complete" as const, reason: "stop" as const };

export function AskPage({
  indexReady = true,
  answer = undefined,
  error: initialError = null,
  hits,
  askedQuery = null,
}: {
  indexReady?: boolean;
  answer?: string | null;
  error?: string | null;
  hits?: AskHit[];
  askedQuery?: string | null;
}) {
  const [draft, setDraft] = useState(askedQuery ?? "");
  const [activeQuery, setActiveQuery] = useState<string | null>(askedQuery);
  const [resultAnswer, setResultAnswer] = useState<string | null>(hits ? (answer ?? null) : null);
  const [resultError, setResultError] = useState<string | null>(initialError);
  const [results, setResults] = useState<AskHit[] | null>(hits ?? null);
  const [answerVote, setAnswerVote] = useState<Vote>(null);
  const [interactionId, setInteractionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [showSources, setShowSources] = useState(true);

  useEffect(() => {
    if (hits !== undefined) return;
    const stored = window.sessionStorage.getItem(ASK_INTERACTION);
    if (!stored) return;
    void feedbackService
      .read(stored)
      .then((interaction) => {
        if (interaction.kind !== "ask") return;
        setDraft(interaction.query);
        setActiveQuery(interaction.query);
        setResultAnswer(interaction.answer);
        setResultError(interaction.error);
        setAnswerVote(interaction.vote ?? null);
        setResults(interaction.hits);
        setInteractionId(interaction.interaction_id);
      })
      .catch(() => undefined);
  }, [hits]);

  const runAsk = async (query: string) => {
    const trimmed = query.trim();
    if (!trimmed) {
      setError("Enter a question.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const response = await askService.ask(trimmed);
      setActiveQuery(trimmed);
      setResultAnswer(response.answer);
      setResultError(response.error);
      setAnswerVote(response.vote ?? null);
      setResults(response.hits);
      setInteractionId(response.interaction_id);
      window.sessionStorage.setItem(ASK_INTERACTION, response.interaction_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to ask. Try again.");
    } finally {
      setPending(false);
    }
  };

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    await runAsk(draft);
  };

  const runtime = useExternalStoreRuntime({
    isRunning: pending,
    state: null,
    messages: [
      ...(activeQuery
        ? [
            fromThreadMessageLike(
              {
                id: "user-msg",
                role: "user" as const,
                content: [{ type: "text" as const, text: activeQuery }],
              },
              "user-msg",
              COMPLETE_STATUS,
            ),
          ]
        : []),
      ...(resultAnswer
        ? [
            fromThreadMessageLike(
              {
                id: "assistant-msg",
                role: "assistant" as const,
                content: [{ type: "text" as const, text: resultAnswer }],
                status: COMPLETE_STATUS,
              },
              "assistant-msg",
              COMPLETE_STATUS,
            ),
          ]
        : []),
    ],
    onNew: async (message) => {
      const textPart = message.content.find((p) => p.type === "text");
      if (textPart && "text" in textPart) {
        setDraft(textPart.text);
        await runAsk(textPart.text);
      }
    },
  });

  if (!indexReady) {
    return (
      <>
        <TopBar title="Ask" />
        <div className="mx-auto max-w-5xl px-8 py-16">
          <IndexPreparing />
        </div>
      </>
    );
  }

  const shown = results ?? [];
  const empty = activeQuery != null && shown.length === 0 && !pending && !error;

  const chooseAnswer = async (action: "up" | "down" | "clear") => {
    const vote: Vote = action === "clear" ? null : action;
    if (!interactionId) {
      setAnswerVote(vote);
      return;
    }
    const saved = await feedbackService.submit({ interactionId, target: "answer", action });
    setAnswerVote(saved.vote ?? null);
  };

  const choosePassage = async (hit: AskHit, action: "up" | "down" | "clear") => {
    const vote: Vote = action === "clear" ? null : action;
    if (!interactionId) {
      setResults((current) =>
        (current ?? []).map((item) => (item.rank === hit.rank ? { ...item, vote } : item)),
      );
      return;
    }
    const saved = await feedbackService.submit({
      interactionId,
      target: "passage",
      rank: hit.rank,
      action,
    });
    setResults((current) =>
      (current ?? []).map((item) =>
        item.rank === hit.rank ? { ...item, vote: saved.vote ?? null } : item,
      ),
    );
  };

  return (
    <>
      <TopBar title="Ask" />
      <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-4 py-6 sm:px-6 lg:px-8">
        <div className="flex flex-1 flex-col gap-6 lg:flex-row lg:items-start">
          {/* Main Chat / Gemini-style Conversation Pane */}
          <div className="flex min-w-0 flex-1 flex-col gap-6">
            <AssistantRuntimeProvider runtime={runtime}>
              <ThreadPrimitive.Root className="flex flex-col gap-6">
                <ThreadPrimitive.Empty>
                  <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border/80 bg-gradient-to-b from-muted/30 to-muted/10 px-6 py-16 text-center">
                    <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary shadow-sm ring-1 ring-primary/20">
                      <Sparkles className="h-7 w-7" />
                    </div>
                    <h2 className="mt-5 text-xl font-semibold tracking-tight text-foreground">
                      Ask anything about your corpus
                    </h2>
                    <p className="mt-2 max-w-lg text-sm leading-relaxed text-muted-foreground">
                      DeepFix retrieves the most relevant passages from your indexed documents and
                      synthesizes an answer grounded directly in source evidence.
                    </p>
                    <div className="mt-6 flex flex-wrap justify-center gap-2">
                      <button
                        type="button"
                        onClick={() => {
                          setDraft("Summarize the key topics in the uploaded documents.");
                          void runAsk("Summarize the key topics in the uploaded documents.");
                        }}
                        className="rounded-full border border-border/60 bg-background/80 px-3.5 py-1.5 text-xs font-medium text-foreground transition-all hover:border-primary/50 hover:bg-primary/5"
                      >
                        ✨ Summarize key topics
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setDraft("What are the main problem types or definitions presented?");
                          void runAsk("What are the main problem types or definitions presented?");
                        }}
                        className="rounded-full border border-border/60 bg-background/80 px-3.5 py-1.5 text-xs font-medium text-foreground transition-all hover:border-primary/50 hover:bg-primary/5"
                      >
                        🔍 Explain main definitions
                      </button>
                    </div>
                  </div>
                </ThreadPrimitive.Empty>

                {/* Conversation Messages Viewport */}
                <div className="flex flex-col gap-5">
                  {/* User query bubble */}
                  {activeQuery ? (
                    <div className="flex items-start justify-end gap-3.5">
                      <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-primary px-4 py-3 text-sm text-primary-foreground shadow-sm">
                        <p className="whitespace-pre-wrap">{activeQuery}</p>
                      </div>
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/20 text-primary ring-1 ring-primary/30">
                        <UserIcon className="h-4 w-4" />
                      </div>
                    </div>
                  ) : null}

                  {/* Thinking / Reasoning state indicator */}
                  {pending ? (
                    <div className="flex items-start gap-3.5 animate-in fade-in duration-300">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-sm ring-4 ring-primary/10">
                        <Bot className="h-4 w-4" />
                      </div>
                      <Card className="flex flex-1 flex-col gap-3 rounded-2xl rounded-tl-sm border-border/60 bg-card p-5 shadow-soft">
                        <div className="flex items-center gap-2.5 text-sm font-medium text-primary">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          <span className="animate-pulse">DeepFix is thinking & searching corpus…</span>
                        </div>
                        <div className="flex flex-col gap-2 pt-1">
                          <div className="h-2 w-3/4 animate-pulse rounded-full bg-muted" />
                          <div className="h-2 w-5/6 animate-pulse rounded-full bg-muted/70" />
                          <div className="h-2 w-1/2 animate-pulse rounded-full bg-muted/50" />
                        </div>
                      </Card>
                    </div>
                  ) : null}

                  {/* Errors display (must precede passages for tests) */}
                  {error ? (
                    <p role="alert" className="text-sm font-medium text-destructive">
                      {error}
                    </p>
                  ) : null}
                  {resultError ? (
                    <p role="alert" className="text-sm font-medium text-destructive">
                      {resultError}
                    </p>
                  ) : null}

                  {/* Assistant response card */}
                  {resultAnswer && !pending ? (
                    <div className="flex items-start gap-3.5 animate-in fade-in duration-300">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-sm ring-4 ring-primary/10">
                        <Bot className="h-4 w-4" />
                      </div>
                      <Card className="flex flex-1 flex-col gap-4 rounded-2xl rounded-tl-sm border-border bg-card p-6 shadow-soft">
                        <div className="flex items-center justify-between border-b border-border/50 pb-3">
                          <div className="flex items-center gap-2">
                            <span className="text-label-lg font-semibold text-foreground">Answer</span>
                            <span className="inline-flex items-center rounded-full border border-primary/20 bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                              Grounded
                            </span>
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="text-xs text-muted-foreground">Generated from corpus</span>
                            {shown.length > 0 ? (
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => setShowSources((prev) => !prev)}
                                className="hidden h-7 gap-1.5 px-2 text-xs lg:inline-flex"
                              >
                                {showSources ? (
                                  <PanelRightClose className="h-3.5 w-3.5" />
                                ) : (
                                  <PanelRightOpen className="h-3.5 w-3.5" />
                                )}
                                <span>{showSources ? "Hide sources" : `Sources (${shown.length})`}</span>
                              </Button>
                            ) : null}
                          </div>
                        </div>

                        <div className="prose prose-sm dark:prose-invert max-w-none text-body-md text-foreground">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{resultAnswer}</ReactMarkdown>
                        </div>

                        <div className="flex items-center justify-between border-t border-border/40 pt-3">
                          <VoteButtons
                            vote={answerVote}
                            upLabel="Mark answer as good"
                            downLabel="Mark answer as bad"
                            onAction={(action) => void chooseAnswer(action)}
                          />
                          <span className="text-[11px] text-muted-foreground">
                            Evaluated with DeepFix RAG Engine
                          </span>
                        </div>
                      </Card>
                    </div>
                  ) : null}
                </div>

                {empty ? (
                  <p className="text-body-md text-muted-foreground">
                    No passages matched &quot;{activeQuery}&quot;.
                  </p>
                ) : null}

                {/* Assistant UI Composer (Chat Input) */}
                <ComposerPrimitive.Root
                  asChild
                  className="relative flex items-end gap-2 rounded-2xl border border-input bg-card p-2 shadow-sm transition-all focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/20"
                  aria-label="Ask composer"
                >
                  <form onSubmit={onSubmit} className="flex w-full items-end gap-2">
                    <label className="sr-only" htmlFor="ask-input">
                      Ask
                    </label>
                    <Input
                      id="ask-input"
                      aria-label="Ask"
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      placeholder="Ask a question about the corpus..."
                      disabled={pending}
                      aria-busy={pending}
                      className="h-11 min-h-[44px] flex-1 border-0 bg-transparent px-3 py-2 text-base shadow-none focus-visible:ring-0 md:text-sm"
                    />
                    <Button
                      type="submit"
                      size="icon"
                      className="h-10 w-10 shrink-0 rounded-xl transition-all"
                      disabled={pending || !draft.trim()}
                      aria-busy={pending}
                      aria-label="Ask"
                    >
                      {pending ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <ArrowUp className="h-4 w-4" />
                      )}
                    </Button>
                  </form>
                </ComposerPrimitive.Root>
              </ThreadPrimitive.Root>
            </AssistantRuntimeProvider>
          </div>

          {/* Side-by-Side Vertical Scrollable Grounding Passages Drawer */}
          {shown.length > 0 && showSources ? (
            <aside className="w-full shrink-0 lg:w-96 xl:w-[26rem]">
              <section className="flex flex-col gap-3.5 rounded-2xl border border-border/80 bg-muted/20 p-4 shadow-sm">
                <div className="flex items-center justify-between border-b border-border/60 pb-2.5">
                  <div className="flex items-center gap-2">
                    <BookOpen className="h-4 w-4 text-primary" />
                    <h2 className="text-sm font-semibold uppercase tracking-wider text-foreground">
                      Passages
                    </h2>
                  </div>
                  <span className="rounded-full bg-background px-2 py-0.5 text-xs font-medium text-muted-foreground ring-1 ring-border">
                    {shown.length} {shown.length === 1 ? "source" : "sources"}
                  </span>
                </div>

                <ol className="flex max-h-[calc(100vh-14rem)] flex-col gap-3 overflow-y-auto pr-1">
                  {shown.map((hit) => (
                    <li key={`${hit.doc_id}-${hit.rank}`}>
                      <Card className="flex flex-col gap-2 rounded-xl border border-border bg-card p-4 shadow-soft transition-all hover:border-primary/50 hover:shadow-md">
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-1.5 overflow-hidden">
                            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-primary/10 font-mono text-xs font-semibold text-primary">
                              #{hit.rank}
                            </span>
                            <span className="truncate text-xs font-medium text-foreground" title={hit.document_filename}>
                              {hit.document_filename}
                            </span>
                          </div>
                          <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
                            {hit.score.toFixed(3)}
                          </span>
                        </div>
                        <p className="line-clamp-6 text-xs leading-relaxed text-foreground/85 hover:line-clamp-none">
                          {hit.text}
                        </p>
                        <div className="border-t border-border/40 pt-2">
                          <VoteButtons
                            vote={hit.vote ?? null}
                            upLabel="Mark passage as relevant"
                            downLabel="Mark passage as not relevant"
                            onAction={(action) => void choosePassage(hit, action)}
                          />
                        </div>
                      </Card>
                    </li>
                  ))}
                </ol>
              </section>
            </aside>
          ) : null}
        </div>
      </div>
    </>
  );
}
