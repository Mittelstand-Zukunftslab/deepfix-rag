import { useEffect, useMemo, useRef, useState } from "react";
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

import { ChatComposer } from "@/features/admin-chat/chat-composer";
import { ChatMessageView } from "@/features/admin-chat/chat-message";
import {
  createThreadChat,
  errorMessage,
  type ChatHooks,
  type ThreadChat,
} from "@/features/admin-chat/chat-transport";
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
  disabled,
}: {
  session: ThreadChat;
  /** The stored turn a page load found still running, resumed once on mount. */
  runningTurnId: string | null;
  disabled: boolean;
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
      <ThreadBody disabled={disabled} />
    </AssistantRuntimeProvider>
  );
}

function ThreadBody({ disabled }: { disabled: boolean }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-6">
      <div aria-live="polite" className="flex flex-col gap-3" data-testid="admin-chat-stream">
        <ThreadPrimitive.Empty>
          <p className="text-body-md text-muted-foreground">
            No messages yet. Ask a question to start.
          </p>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages>{() => <MessageRow />}</ThreadPrimitive.Messages>
        <ThreadError />
      </div>
      <div className="sticky bottom-0 mt-auto bg-background pb-2">
        <ChatComposer disabled={disabled} />
      </div>
    </div>
  );
}

function MessageRow() {
  const role = useAuiState((state) => state.message.role);
  const id = useAuiState((state) => state.message.id);
  if (role === "user") {
    return (
      <div data-testid={`chat-message-${id}`} data-role="user">
        <MessagePrimitive.Root className="flex justify-end">
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
  return (
    <div data-testid={`chat-message-${id}`} data-role="assistant" className="group/message">
      <MessagePrimitive.Parts
        components={{
          Text: ({ text }) => <ChatMessageView role="assistant" content={text} />,
          data: { by_name: { "deepfix-tool": ToolRow } },
        }}
      />
      {isLast ? (
        <ActionBarPrimitive.Root
          hideWhenRunning
          autohide="not-last"
          className="mt-1 flex gap-1 opacity-0 transition-opacity group-hover/message:opacity-100 group-focus-within/message:opacity-100"
        >
          <ActionBarPrimitive.Reload className="rounded px-1.5 py-1 text-label-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground">
            Regenerate
          </ActionBarPrimitive.Reload>
        </ActionBarPrimitive.Root>
      ) : null}
    </div>
  );
}

function ToolRow({ data }: { data: ChatToolProgress }) {
  return (
    <p className="text-label-sm text-muted-foreground">{data.label ?? data.tool ?? "Working…"}</p>
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
