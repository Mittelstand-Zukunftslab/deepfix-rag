import {
  forwardRef,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
} from "react";
import {
  ActionBarPrimitive,
  AssistantRuntimeProvider,
  MessagePrimitive,
  ThreadPrimitive,
  useAui,
  useAuiState,
  type ThreadMessage,
} from "@assistant-ui/react";
import { useAISDKRuntime, useAISDKError } from "@assistant-ui/react-ai-sdk";
import { useChat, type UseChatHelpers } from "@ai-sdk/react";
import type { UIMessage } from "ai";
import type { ReactNode } from "react";
import { ArrowDown, Copy, RefreshCw, Wrench } from "lucide-react";

import { ChatComposer } from "@/features/admin-chat/chat-composer";
import { ThreadWelcome } from "@/features/admin-chat/thread-welcome";
import { ChatMessageView } from "@/features/admin-chat/chat-message";
import {
  createThreadChat,
  errorMessage,
  type ChatHooks,
  type ThreadChat,
} from "@/features/admin-chat/chat-transport";
import { cn } from "@/lib/utils";
import type { ChatToolProgress } from "@/schemas/admin-chat";

/**
 * One Assistant UI runtime per thread.
 *
 * The runtime itself is what renders the messages, so nothing is appended by
 * hand: a streamed reply and the stored history cannot both be drawn for the
 * same text. The session's `Chat` outlives this component, which is why a reply
 * still streaming when the admin switches threads finishes into its own thread
 * and is there when they switch back.
 */

/** The chat sessions the page has opened, keyed by thread id. */
export function useChatSessions() {
  const sessions = useRef(new Map<string, ThreadChat>());

  return useMemo(
    () => ({
      ensure(threadId: string, seed: UIMessage[], hooks: { current: ChatHooks }) {
        const existing = sessions.current.get(threadId);
        if (existing) return existing;
        const created = createThreadChat(threadId, seed, hooks);
        sessions.current.set(threadId, created);
        return created;
      },
      drop(threadId: string) {
        const session = sessions.current.get(threadId);
        if (!session) return;
        void session.chat.stop();
        sessions.current.delete(threadId);
      },
    }),
    [],
  );
}

/** The latest handlers, without rebuilding a transport every render. */
export function useChatHooks(handlers: ChatHooks): { current: ChatHooks } {
  const ref = useRef(handlers);
  ref.current = handlers;
  return ref;
}

/** The runtime for one thread, plus the composer, the stop control, and a retry. */
export function ChatThread({
  session,
  runningTurnId,
  initialPrompt,
  disabled,
  children,
}: {
  session: ThreadChat;
  /** The stored turn a page load found still running, resumed once on mount. */
  runningTurnId: string | null;
  /** A welcome chip's prompt, placed in the composer rather than sent (CHAT-019). */
  initialPrompt?: string | undefined;
  disabled: boolean;
  /** The action cards and run panels, drawn in stream order above the composer. */
  children?: ReactNode;
}) {
  const helpers = useChat<UIMessage>({ chat: session.chat });
  const runtime = useAISDKRuntime(helpers);

  useEffect(() => {
    if (!runningTurnId) return;
    session.resumeTurnId.current = runningTurnId;
    // The stored partial text is already seeded; the reconnect replays it from
    // the turn's own id, so it replaces that message rather than duplicating it.
    void helpers.resumeStream().catch(() => undefined);
    // Resume once per stored turn: `helpers` is a fresh object every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runningTurnId, session]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ComposerSeed prompt={initialPrompt} />
      <ThreadBody disabled={disabled}>{children}</ThreadBody>
    </AssistantRuntimeProvider>
  );
}

/**
 * Places a chip's prompt in the composer once, without sending it.
 *
 * It has to be a child of the provider: `useAui` reads the runtime the provider
 * installs, and the thread component itself renders above it.
 */
function ComposerSeed({ prompt }: { prompt: string | undefined }) {
  const aui = useAui();
  const seeded = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!prompt || seeded.current === prompt) return;
    seeded.current = prompt;
    aui.composer.setText(prompt);
  }, [aui, prompt]);
  return null;
}

function ThreadBody({ disabled, children }: { disabled: boolean; children?: ReactNode }) {
  return (
    // `ThreadPrimitive.Root` is the scope a suggestion writes its prompt into.
    <ThreadPrimitive.Root className="relative flex min-h-0 flex-1 flex-col gap-6">
      <ThreadWelcome />
      <div aria-live="polite" className="flex flex-col gap-6" data-testid="admin-chat-stream">
        <ThreadPrimitive.Messages>{() => <MessageRow />}</ThreadPrimitive.Messages>
        <ThreadError />
        {children}
      </div>
      <div className="relative sticky bottom-0 mt-auto bg-background pb-2">
        <ThreadPrimitive.ScrollToBottom asChild>
          <button
            type="button"
            aria-label="Scroll to bottom"
            className="absolute -top-12 start-1/2 flex size-9 -translate-x-1/2 items-center justify-center rounded-full border border-border bg-background text-muted-foreground shadow-soft transition-colors hover:text-foreground disabled:invisible"
          >
            <ArrowDown className="size-4" aria-hidden />
          </button>
        </ThreadPrimitive.ScrollToBottom>
        <ChatComposer disabled={disabled} />
      </div>
    </ThreadPrimitive.Root>
  );
}

function MessageRow() {
  const role = useAuiState((state) => state.message.role);
  const id = useAuiState((state) => state.message.id);
  if (role === "user") {
    return (
      <div data-testid={`chat-message-${id}`} data-role="user">
        <MessagePrimitive.Root>
          <MessagePrimitive.Parts
            components={{
              Text: ({ text }) => <ChatMessageView role="user" content={text} />,
            }}
          />
        </MessagePrimitive.Root>
      </div>
    );
  }
  return <AssistantMessage id={id} />;
}

function AssistantMessage({ id }: { id: string }) {
  const isLast = useAuiState((state) => state.message.isLast);
  const isRunning = useAuiState((state) => state.message.status?.type === "running");
  return (
    <div data-testid={`chat-message-${id}`} data-role="assistant" className="group/message">
      <MessagePrimitive.Parts
        components={{
          Text: ({ text }) => <ChatMessageView role="assistant" content={text} />,
          data: { by_name: { "deepfix-tool": ToolRow } },
        }}
      />
      {isRunning ? (
        <p
          role="status"
          className="mt-2 flex items-center gap-1.5 text-label-sm text-muted-foreground"
        >
          <span className="size-1.5 animate-pulse rounded-full bg-primary motion-reduce:animate-none" />
          Working…
        </p>
      ) : null}
      {isLast ? (
        <ActionBarPrimitive.Root
          hideWhenRunning
          autohide="not-last"
          className="mt-2 flex items-center gap-1 transition-opacity group-hover/message:opacity-100 group-focus-within/message:opacity-100"
        >
          <ActionBarPrimitive.Copy asChild>
            <IconButton aria-label="Copy">
              <Copy className="size-3.5" aria-hidden />
            </IconButton>
          </ActionBarPrimitive.Copy>
          <ActionBarPrimitive.Reload asChild>
            <IconButton aria-label="Regenerate">
              <RefreshCw className="size-3.5" aria-hidden />
            </IconButton>
          </ActionBarPrimitive.Reload>
        </ActionBarPrimitive.Root>
      ) : null}
    </div>
  );
}

/** A small action-bar control that forwards the primitive's props, so `asChild` still works. */
const IconButton = forwardRef<HTMLButtonElement, ComponentPropsWithoutRef<"button">>(
  function IconButton({ className, children, ...props }, ref) {
    return (
      <button
        ref={ref}
        type="button"
        className={cn(
          "flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors",
          "hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          className,
        )}
        {...props}
      >
        {children}
      </button>
    );
  },
);

function ToolRow({ data }: { data: ChatToolProgress }) {
  return (
    <p className="mt-2 flex items-center gap-2 rounded-md bg-muted/60 px-2 py-1 text-label-sm text-muted-foreground">
      <Wrench className="size-3" aria-hidden />
      {data.label ?? data.tool ?? "Working…"}
    </p>
  );
}

function ThreadError() {
  const error = useAISDKError();
  const isRunning = useAuiState((state) => state.thread.isRunning);
  if (!error || isRunning) return null;
  return (
    <p role="alert" className="text-label-sm text-destructive">
      {errorMessage(error)}
    </p>
  );
}
