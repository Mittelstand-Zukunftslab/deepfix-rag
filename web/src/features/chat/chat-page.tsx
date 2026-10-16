import { useEffect, useRef, useState, type FormEvent } from "react";
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
  Check,
  ChevronRight,
  Copy,
  Edit3,
  ExternalLink,
  FileText,
  History,
  Loader2,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Sparkles,
  Trash2,
  User as UserIcon,
  X,
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

const SESSIONS_STORAGE_KEY = "deepfix.chat_sessions.v2";
const ACTIVE_SESSION_STORAGE_KEY = "deepfix.active_session_id.v2";

/** Chats are private: each signed-in user keeps their history under their own keys. */
export function chatStorageKeys(ownerId: string): { sessions: string; active: string } {
  return {
    sessions: `${SESSIONS_STORAGE_KEY}:${ownerId}`,
    active: `${ACTIVE_SESSION_STORAGE_KEY}:${ownerId}`,
  };
}

function dropUnscopedHistory(): void {
  try {
    window.localStorage.removeItem(SESSIONS_STORAGE_KEY);
    window.localStorage.removeItem(ACTIVE_SESSION_STORAGE_KEY);
  } catch {
    // Ignore localStorage errors
  }
}
const COMPLETE_STATUS = { type: "complete" as const, reason: "stop" as const };

export interface ChatTurn {
  id: string;
  userQuery: string;
  timestamp: number;
  answer: string | null;
  error: string | null;
  hits: AskHit[];
  answerVote: Vote;
  interactionId: string | null;
}

export interface ChatSession {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  turns: ChatTurn[];
}

function createNewSession(initialTitle = "New Chat"): ChatSession {
  const now = Date.now();
  return {
    id: `session-${now}-${Math.random().toString(36).slice(2, 7)}`,
    title: initialTitle,
    createdAt: now,
    updatedAt: now,
    turns: [],
  };
}

export function AskPage({
  indexReady = true,
  answer = undefined,
  error: initialError = null,
  hits,
  askedQuery = null,
  ownerId = null,
}: {
  indexReady?: boolean;
  answer?: string | null;
  error?: string | null;
  hits?: AskHit[];
  askedQuery?: string | null;
  ownerId?: string | null;
}) {
  const isControlledMode =
    askedQuery !== null || answer !== undefined || hits !== undefined || initialError !== null;
  const storageKeys = ownerId && !isControlledMode ? chatStorageKeys(ownerId) : null;

  const [sessions, setSessions] = useState<ChatSession[]>(() => {
    if (isControlledMode) {
      const controlledTurn: ChatTurn = {
        id: "controlled-turn",
        userQuery: askedQuery ?? "Query",
        timestamp: Date.now(),
        answer: hits ? (answer ?? null) : null,
        error: initialError,
        hits: hits ?? [],
        answerVote: null,
        interactionId: null,
      };
      return [
        {
          id: "controlled-session",
          title: askedQuery ? askedQuery.slice(0, 28) : "Current Query",
          createdAt: Date.now(),
          updatedAt: Date.now(),
          turns: [controlledTurn],
        },
      ];
    }

    dropUnscopedHistory();
    if (!storageKeys) return [createNewSession()];
    try {
      const raw = window.localStorage.getItem(storageKeys.sessions);
      if (raw) {
        const parsed = JSON.parse(raw) as ChatSession[];
        if (Array.isArray(parsed) && parsed.length > 0) {
          return parsed;
        }
      }
    } catch {
      // Ignore localStorage errors
    }
    return [createNewSession()];
  });

  const [activeSessionId, setActiveSessionId] = useState<string>(() => {
    if (isControlledMode) return "controlled-session";
    try {
      const savedActive = storageKeys ? window.localStorage.getItem(storageKeys.active) : null;
      if (savedActive) return savedActive;
    } catch {
      // Ignore
    }
    return sessions[0]?.id ?? "default";
  });

  const [draft, setDraft] = useState(isControlledMode ? (askedQuery ?? "") : "");
  const [pending, setPending] = useState(false);
  const [pendingQuery, setPendingQuery] = useState<string | null>(null);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null);

  const [showHistory, setShowHistory] = useState(false);
  const [showSources, setShowSources] = useState(true);

  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [copiedTurnId, setCopiedTurnId] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!storageKeys) return;
    try {
      window.localStorage.setItem(storageKeys.sessions, JSON.stringify(sessions));
      if (activeSessionId) {
        window.localStorage.setItem(storageKeys.active, activeSessionId);
      }
    } catch {
      // Ignore
    }
  }, [sessions, activeSessionId, storageKeys?.sessions, storageKeys?.active]);

  const activeSession = sessions.find((s) => s.id === activeSessionId) ?? sessions[0];
  const turns = activeSession?.turns ?? [];

  const currentTurn =
    (selectedTurnId ? turns.find((t) => t.id === selectedTurnId) : null) ??
    (turns.length > 0 ? turns[turns.length - 1] : null);

  const shownHits = currentTurn?.hits ?? [];

  useEffect(() => {
    if (typeof messagesEndRef.current?.scrollIntoView === "function") {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [turns.length, pending]);

  const handleNewChat = () => {
    const fresh = createNewSession();
    setSessions((prev) => [fresh, ...prev]);
    setActiveSessionId(fresh.id);
    setSelectedTurnId(null);
    setDraft("");
    setGlobalError(null);
  };

  const handleDeleteSession = (sessionId: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    setSessions((prev) => {
      const remaining = prev.filter((s) => s.id !== sessionId);
      if (remaining.length === 0) {
        const fresh = createNewSession();
        setActiveSessionId(fresh.id);
        return [fresh];
      }
      if (activeSessionId === sessionId) {
        setActiveSessionId(remaining[0].id);
      }
      return remaining;
    });
  };

  const handleStartRename = (session: ChatSession, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingSessionId(session.id);
    setEditingTitle(session.title);
  };

  const handleSaveRename = (sessionId: string) => {
    const trimmed = editingTitle.trim();
    if (trimmed) {
      setSessions((prev) =>
        prev.map((s) => (s.id === sessionId ? { ...s, title: trimmed, updatedAt: Date.now() } : s)),
      );
    }
    setEditingSessionId(null);
    setEditingTitle("");
  };

  const runAsk = async (queryText: string) => {
    const trimmed = queryText.trim();
    if (!trimmed) {
      setGlobalError("Enter a question.");
      return;
    }

    setDraft("");
    setGlobalError(null);
    setPending(true);
    setPendingQuery(trimmed);

    const turnId = `turn-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

    try {
      const response = await askService.ask(trimmed);

      const completedTurn: ChatTurn = {
        id: turnId,
        userQuery: trimmed,
        timestamp: Date.now(),
        answer: response.answer,
        error: response.error,
        hits: response.hits ?? [],
        answerVote: response.vote ?? null,
        interactionId: response.interaction_id,
      };

      setSessions((prev) =>
        prev.map((session) => {
          if (session.id !== activeSession.id) return session;
          const isFirstTurn = session.turns.length === 0;
          const newTitle =
            isFirstTurn && session.title === "New Chat"
              ? trimmed.length > 32
                ? `${trimmed.slice(0, 32)}…`
                : trimmed
              : session.title;

          return {
            ...session,
            title: newTitle,
            updatedAt: Date.now(),
            turns: [...session.turns, completedTurn],
          };
        }),
      );

      setSelectedTurnId(turnId);
    } catch (caught) {
      const errorMsg = caught instanceof Error ? caught.message : "Unable to ask. Try again.";
      setGlobalError(errorMsg);

      const failedTurn: ChatTurn = {
        id: turnId,
        userQuery: trimmed,
        timestamp: Date.now(),
        answer: null,
        error: errorMsg,
        hits: [],
        answerVote: null,
        interactionId: null,
      };

      setSessions((prev) =>
        prev.map((session) =>
          session.id === activeSession.id
            ? { ...session, turns: [...session.turns, failedTurn], updatedAt: Date.now() }
            : session,
        ),
      );
    } finally {
      setPending(false);
      setPendingQuery(null);
    }
  };

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    await runAsk(draft);
  };

  const chooseAnswer = async (turnId: string, action: "up" | "down" | "clear") => {
    const vote: Vote = action === "clear" ? null : action;
    const turn = turns.find((t) => t.id === turnId);
    if (!turn?.interactionId) {
      setSessions((prev) =>
        prev.map((s) =>
          s.id === activeSession.id
            ? {
                ...s,
                turns: s.turns.map((t) => (t.id === turnId ? { ...t, answerVote: vote } : t)),
              }
            : s,
        ),
      );
      return;
    }

    const saved = await feedbackService.submit({
      interactionId: turn.interactionId,
      target: "answer",
      action,
    });

    setSessions((prev) =>
      prev.map((s) =>
        s.id === activeSession.id
          ? {
              ...s,
              turns: s.turns.map((t) =>
                t.id === turnId ? { ...t, answerVote: saved.vote ?? null } : t,
              ),
            }
          : s,
      ),
    );
  };

  const choosePassage = async (turnId: string, hit: AskHit, action: "up" | "down" | "clear") => {
    const vote: Vote = action === "clear" ? null : action;
    const turn = turns.find((t) => t.id === turnId);
    if (!turn?.interactionId) {
      setSessions((prev) =>
        prev.map((s) =>
          s.id === activeSession.id
            ? {
                ...s,
                turns: s.turns.map((t) =>
                  t.id === turnId
                    ? {
                        ...t,
                        hits: t.hits.map((h) => (h.rank === hit.rank ? { ...h, vote } : h)),
                      }
                    : t,
                ),
              }
            : s,
        ),
      );
      return;
    }

    const saved = await feedbackService.submit({
      interactionId: turn.interactionId,
      target: "passage",
      rank: hit.rank,
      action,
    });

    setSessions((prev) =>
      prev.map((s) =>
        s.id === activeSession.id
          ? {
              ...s,
              turns: s.turns.map((t) =>
                t.id === turnId
                  ? {
                      ...t,
                      hits: t.hits.map((h) =>
                        h.rank === hit.rank ? { ...h, vote: saved.vote ?? null } : h,
                      ),
                    }
                  : t,
              ),
            }
          : s,
      ),
    );
  };

  const handleCopy = (turnId: string, text: string) => {
    void navigator.clipboard.writeText(text);
    setCopiedTurnId(turnId);
    setTimeout(() => {
      setCopiedTurnId(null);
    }, 2000);
  };

  const runtime = useExternalStoreRuntime({
    isRunning: pending,
    state: null,
    messages: turns.flatMap((t) => [
      fromThreadMessageLike(
        {
          id: `user-${t.id}`,
          role: "user" as const,
          content: [{ type: "text" as const, text: t.userQuery }],
        },
        `user-${t.id}`,
        COMPLETE_STATUS,
      ),
      ...(t.answer
        ? [
            fromThreadMessageLike(
              {
                id: `assistant-${t.id}`,
                role: "assistant" as const,
                content: [{ type: "text" as const, text: t.answer }],
                status: COMPLETE_STATUS,
              },
              `assistant-${t.id}`,
              COMPLETE_STATUS,
            ),
          ]
        : []),
    ]),
    onNew: async (message) => {
      const textPart = message.content.find((p) => p.type === "text");
      if (textPart && "text" in textPart) {
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

  const hasEmptyConversation = turns.length === 0 && !pending;

  return (
    <>
      <TopBar title="Ask" />

      <div className="mx-auto flex h-[calc(100vh-4.25rem)] w-full max-w-[96rem] flex-1 overflow-hidden px-2 py-3 sm:px-4 lg:px-6">
        <div className="relative flex flex-1 gap-4 overflow-hidden">
          {/* 1. Left Chat History Sidebar (Collapsible, Perplexity/ChatGPT style) */}
          <aside
            className={`flex flex-col border-r border-border/70 bg-card/60 backdrop-blur-md transition-all duration-300 ease-in-out ${
              showHistory
                ? "w-72 shrink-0 rounded-2xl border p-3 shadow-md lg:static lg:z-0"
                : "w-0 -translate-x-full overflow-hidden p-0 opacity-0 lg:w-0"
            }`}
          >
            {showHistory && (
              <div className="flex h-full flex-col gap-3">
                <div className="flex items-center justify-between border-b border-border/50 pb-2.5">
                  <div className="flex items-center gap-2">
                    <History className="h-4 w-4 text-primary" />
                    <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      Chat History
                    </span>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 rounded-lg text-muted-foreground hover:text-foreground"
                    onClick={() => setShowHistory(false)}
                    title="Close history"
                  >
                    <PanelLeftClose className="h-4 w-4" />
                  </Button>
                </div>

                <Button
                  onClick={handleNewChat}
                  variant="outline"
                  className="group flex w-full items-center justify-start gap-2.5 rounded-xl border-dashed border-primary/40 bg-primary/5 px-3 py-2 text-xs font-medium text-foreground transition-all hover:border-primary hover:bg-primary/10"
                >
                  <Plus className="h-4 w-4 text-primary transition-transform group-hover:rotate-90" />
                  <span>New Chat</span>
                </Button>

                <div className="flex flex-1 flex-col gap-1 overflow-y-auto pr-1">
                  {sessions.map((s) => {
                    const isActive = s.id === activeSession.id;
                    const isEditing = editingSessionId === s.id;

                    return (
                      <div
                        key={s.id}
                        onClick={() => {
                          setActiveSessionId(s.id);
                          setSelectedTurnId(null);
                        }}
                        className={`group relative flex cursor-pointer items-center justify-between rounded-xl px-3 py-2 text-xs transition-all ${
                          isActive
                            ? "bg-primary/15 font-semibold text-primary shadow-xs ring-1 ring-primary/25"
                            : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                        }`}
                      >
                        <div className="flex min-w-0 flex-1 items-center gap-2">
                          <MessageSquare className="h-3.5 w-3.5 shrink-0" />
                          {isEditing ? (
                            <form
                              onSubmit={(e) => {
                                e.preventDefault();
                                handleSaveRename(s.id);
                              }}
                              onClick={(e) => e.stopPropagation()}
                              className="flex flex-1 items-center gap-1"
                            >
                              <Input
                                autoFocus
                                value={editingTitle}
                                onChange={(e) => setEditingTitle(e.target.value)}
                                onBlur={() => handleSaveRename(s.id)}
                                className="h-6 text-xs px-1.5 py-0.5"
                              />
                              <button type="submit" className="text-primary hover:text-primary/80">
                                <Check className="h-3.5 w-3.5" />
                              </button>
                            </form>
                          ) : (
                            <span className="truncate">{s.title}</span>
                          )}
                        </div>

                        {!isEditing && (
                          <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                            <button
                              type="button"
                              onClick={(e) => handleStartRename(s, e)}
                              className="rounded p-1 text-muted-foreground hover:bg-background hover:text-foreground"
                              title="Rename session"
                            >
                              <Edit3 className="h-3 w-3" />
                            </button>
                            {sessions.length > 1 && (
                              <button
                                type="button"
                                onClick={(e) => handleDeleteSession(s.id, e)}
                                className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                                title="Delete session"
                              >
                                <Trash2 className="h-3 w-3" />
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </aside>

          {/* 2. Main Center Chat Panel (Multi-turn Conversation Stream) */}
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-border/80 bg-background/50 shadow-sm backdrop-blur-sm">
            {/* Header Toolbar: History toggle & Active Session Title */}
            <div className="flex h-12 shrink-0 items-center justify-between border-b border-border/60 bg-muted/20 px-4">
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowHistory((prev) => !prev)}
                  className="h-8 gap-1.5 px-2.5 text-xs text-muted-foreground hover:text-foreground"
                  title="Toggle Chat History"
                >
                  {showHistory ? (
                    <PanelLeftClose className="h-4 w-4" />
                  ) : (
                    <PanelLeftOpen className="h-4 w-4" />
                  )}
                  <span className="hidden sm:inline">History</span>
                </Button>
                <div className="h-4 w-[1px] bg-border/80" />
                <span className="max-w-[200px] truncate text-xs font-medium text-foreground sm:max-w-xs md:max-w-md">
                  {activeSession?.title}
                </span>
              </div>

              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleNewChat}
                  className="h-7 gap-1 rounded-lg px-2 text-xs font-normal"
                >
                  <Plus className="h-3.5 w-3.5 text-primary" />
                  <span className="hidden sm:inline">New Chat</span>
                </Button>

                {shownHits.length > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setShowSources((prev) => !prev)}
                    className="h-7 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground"
                  >
                    {showSources ? (
                      <PanelRightClose className="h-3.5 w-3.5" />
                    ) : (
                      <PanelRightOpen className="h-3.5 w-3.5" />
                    )}
                    <span>{showSources ? "Hide sources" : `Sources (${shownHits.length})`}</span>
                  </Button>
                )}
              </div>
            </div>

            {/* Scrollable Conversation History Viewport */}
            <AssistantRuntimeProvider runtime={runtime}>
              <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-6">
                <div className="mx-auto flex max-w-3xl flex-col gap-6">
                  <ThreadPrimitive.Root className="flex flex-col gap-6">
                    {/* Empty State / Welcome Screen with quick prompts */}
                    {hasEmptyConversation && (
                      <ThreadPrimitive.Empty>
                        <div className="flex flex-col items-center justify-center rounded-3xl border border-dashed border-border/80 bg-gradient-to-b from-muted/30 via-muted/15 to-transparent px-6 py-14 text-center sm:px-10">
                          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary shadow-sm ring-1 ring-primary/20">
                            <Sparkles className="h-7 w-7" />
                          </div>
                          <h2 className="mt-5 text-xl font-bold tracking-tight text-foreground sm:text-2xl">
                            Ask anything about your corpus
                          </h2>
                          <p className="mt-2.5 max-w-md text-sm leading-relaxed text-muted-foreground">
                            DeepFix searches your indexed documents, cites exact passages, and
                            synthesizes grounded answers powered by multi-stage retrieval.
                          </p>
                          <div className="mt-7 flex flex-wrap justify-center gap-2.5">
                            <button
                              type="button"
                              onClick={() => {
                                void runAsk("Summarize the key topics in the uploaded documents.");
                              }}
                              className="rounded-full border border-border/80 bg-card px-4 py-2 text-xs font-medium text-foreground shadow-xs transition-all hover:border-primary/50 hover:bg-primary/5 hover:text-primary hover:shadow-sm"
                            >
                              ✨ Summarize key topics
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                void runAsk(
                                  "What are the main problem types or definitions presented?",
                                );
                              }}
                              className="rounded-full border border-border/80 bg-card px-4 py-2 text-xs font-medium text-foreground shadow-xs transition-all hover:border-primary/50 hover:bg-primary/5 hover:text-primary hover:shadow-sm"
                            >
                              🔍 Explain core definitions
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                void runAsk("List any actionable steps or recommendations.");
                              }}
                              className="rounded-full border border-border/80 bg-card px-4 py-2 text-xs font-medium text-foreground shadow-xs transition-all hover:border-primary/50 hover:bg-primary/5 hover:text-primary hover:shadow-sm"
                            >
                              📋 Actionable recommendations
                            </button>
                          </div>
                        </div>
                      </ThreadPrimitive.Empty>
                    )}

                    {/* Multi-Turn Messages: Render all previous turns */}
                    {turns.map((turn) => (
                      <div key={turn.id} className="flex flex-col gap-4">
                        {/* User Query Bubble */}
                        <div className="flex items-start justify-end gap-3.5">
                          <div className="max-w-[85%] rounded-2xl rounded-tr-xs bg-primary px-4 py-3 text-sm text-primary-foreground shadow-sm">
                            <p className="whitespace-pre-wrap leading-relaxed">{turn.userQuery}</p>
                          </div>
                          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/20 text-primary ring-1 ring-primary/30">
                            <UserIcon className="h-4 w-4" />
                          </div>
                        </div>

                        {/* Error Message if Generation Failed */}
                        {turn.error && (
                          <div className="flex items-start gap-3.5">
                            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-destructive/15 text-destructive ring-1 ring-destructive/30">
                              <Bot className="h-4 w-4" />
                            </div>
                            <p role="alert" className="text-sm font-medium text-destructive pt-1.5">
                              {turn.error}
                            </p>
                          </div>
                        )}

                        {/* Assistant Grounded Answer Card */}
                        {turn.answer && (
                          <div className="flex items-start gap-3.5">
                            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-xs ring-4 ring-primary/10">
                              <Bot className="h-4 w-4" />
                            </div>

                            <Card className="flex flex-1 flex-col gap-4 rounded-2xl rounded-tl-xs border-border/80 bg-card p-5 shadow-soft transition-all hover:border-border">
                              <div className="flex items-center justify-between border-b border-border/50 pb-3">
                                <div className="flex items-center gap-2">
                                  <span className="text-label-lg font-semibold text-foreground">
                                    Answer
                                  </span>
                                  <Badge
                                    variant="outline"
                                    className="border-primary/25 bg-primary/10 text-[11px] font-medium text-primary"
                                  >
                                    Grounded
                                  </Badge>
                                </div>

                                <div className="flex items-center gap-2">
                                  <span className="text-xs text-muted-foreground">
                                    Generated from corpus
                                  </span>
                                  {turn.hits.length > 0 && (
                                    <Button
                                      variant={selectedTurnId === turn.id ? "secondary" : "ghost"}
                                      size="sm"
                                      onClick={() => {
                                        setSelectedTurnId(turn.id);
                                        setShowSources(true);
                                      }}
                                      className="h-6 gap-1 px-2 text-[11px] font-medium text-primary"
                                      title="Inspect source passages for this answer"
                                    >
                                      <BookOpen className="h-3 w-3" />
                                      <span>{turn.hits.length} sources</span>
                                    </Button>
                                  )}
                                </div>
                              </div>

                              <div className="prose prose-sm dark:prose-invert max-w-none text-body-md leading-relaxed text-foreground">
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                  {turn.answer}
                                </ReactMarkdown>
                              </div>

                              <div className="flex items-center justify-between border-t border-border/40 pt-3">
                                <div className="flex items-center gap-2">
                                  <VoteButtons
                                    vote={turn.answerVote}
                                    upLabel="Mark answer as good"
                                    downLabel="Mark answer as bad"
                                    onAction={(action) => void chooseAnswer(turn.id, action)}
                                  />
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    onClick={() => handleCopy(turn.id, turn.answer ?? "")}
                                    className="h-8 w-8 rounded-lg text-muted-foreground hover:text-foreground"
                                    title="Copy answer"
                                  >
                                    {copiedTurnId === turn.id ? (
                                      <Check className="h-3.5 w-3.5 text-emerald-500" />
                                    ) : (
                                      <Copy className="h-3.5 w-3.5" />
                                    )}
                                  </Button>
                                </div>

                                <span className="text-[11px] text-muted-foreground">
                                  Evaluated with DeepFix RAG Engine
                                </span>
                              </div>
                            </Card>
                          </div>
                        )}
                      </div>
                    ))}

                    {/* Active Pending Turn (Thinking State) */}
                    {pending && pendingQuery && (
                      <div className="flex flex-col gap-4">
                        {/* User Query Bubble */}
                        <div className="flex items-start justify-end gap-3.5">
                          <div className="max-w-[85%] rounded-2xl rounded-tr-xs bg-primary px-4 py-3 text-sm text-primary-foreground shadow-sm">
                            <p className="whitespace-pre-wrap leading-relaxed">{pendingQuery}</p>
                          </div>
                          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/20 text-primary ring-1 ring-primary/30">
                            <UserIcon className="h-4 w-4" />
                          </div>
                        </div>

                        {/* Animated Thinking Card */}
                        <div className="flex items-start gap-3.5 animate-in fade-in duration-300">
                          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-xs ring-4 ring-primary/10">
                            <Bot className="h-4 w-4" />
                          </div>
                          <Card className="flex flex-1 flex-col gap-3 rounded-2xl rounded-tl-xs border-border/70 bg-card p-5 shadow-soft">
                            <div className="flex items-center gap-2.5 text-sm font-medium text-primary">
                              <Loader2 className="h-4 w-4 animate-spin text-primary" />
                              <span className="animate-pulse">
                                DeepFix is searching corpus & synthesizing grounded answer…
                              </span>
                            </div>
                            <div className="flex flex-col gap-2 pt-1">
                              <div className="h-2 w-3/4 animate-pulse rounded-full bg-muted" />
                              <div className="h-2 w-5/6 animate-pulse rounded-full bg-muted/70" />
                              <div className="h-2 w-1/2 animate-pulse rounded-full bg-muted/50" />
                            </div>
                          </Card>
                        </div>
                      </div>
                    )}

                    {globalError && (
                      <p role="alert" className="text-sm font-medium text-destructive">
                        {globalError}
                      </p>
                    )}

                    <div ref={messagesEndRef} />
                  </ThreadPrimitive.Root>
                </div>
              </div>

              {/* 3. Bottom Prompt Composer (Gemini / ChatGPT Style Floating Input) */}
              <div className="border-t border-border/60 bg-background/95 p-3 backdrop-blur-md sm:p-4">
                <div className="mx-auto max-w-3xl">
                  <ComposerPrimitive.Root
                    asChild
                    className="relative flex items-center gap-2 rounded-2xl border border-input bg-card/90 px-3 py-1.5 shadow-sm transition-all focus-within:border-primary/60 focus-within:ring-2 focus-within:ring-primary/20 hover:border-border"
                    aria-label="Ask composer"
                  >
                    <form onSubmit={onSubmit} className="flex w-full items-center gap-2">
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
                        className="h-10 min-h-[40px] flex-1 border-0 bg-transparent px-2 py-2 text-base shadow-none focus-visible:ring-0 md:text-sm"
                      />
                      <Button
                        type="submit"
                        size="icon"
                        className="h-9 w-9 shrink-0 rounded-xl transition-all"
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
                  <p className="mt-1.5 text-center text-[11px] text-muted-foreground">
                    DeepFix searches indexed documents and grounds answers with citations.
                  </p>
                </div>
              </div>
            </AssistantRuntimeProvider>
          </div>

          {/* 4. Right Side-by-Side Vertical Scrollable Grounding Passages Drawer */}
          {shownHits.length > 0 && showSources ? (
            <aside className="w-full shrink-0 lg:w-96 xl:w-[26rem]">
              <section className="flex h-full flex-col gap-3 rounded-2xl border border-border/80 bg-muted/20 p-4 shadow-sm">
                <div className="flex items-center justify-between border-b border-border/60 pb-2.5">
                  <div className="flex items-center gap-2">
                    <BookOpen className="h-4 w-4 text-primary" />
                    <h2 className="text-sm font-semibold uppercase tracking-wider text-foreground">
                      Passages
                    </h2>
                  </div>
                  <span className="rounded-full bg-background px-2.5 py-0.5 text-xs font-medium text-muted-foreground ring-1 ring-border">
                    {shownHits.length} {shownHits.length === 1 ? "source" : "sources"}
                  </span>
                </div>

                <ol className="flex flex-1 flex-col gap-3 overflow-y-auto pr-1">
                  {shownHits.map((hit) => (
                    <li key={`${hit.doc_id}-${hit.rank}`}>
                      <Card className="flex flex-col gap-2 rounded-xl border border-border bg-card p-4 shadow-soft transition-all hover:border-primary/50 hover:shadow-md">
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-1.5 overflow-hidden">
                            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-primary/10 font-mono text-xs font-semibold text-primary">
                              #{hit.rank}
                            </span>
                            <span
                              className="truncate text-xs font-medium text-foreground"
                              title={hit.document_filename}
                            >
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
                            onAction={(action) =>
                              currentTurn && void choosePassage(currentTurn.id, hit, action)
                            }
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
