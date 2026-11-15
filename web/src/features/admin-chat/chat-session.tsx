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
      <ThreadBody helpers={helpers} disabled={disabled} />
    </AssistantRuntimeProvider>
  );
}

function ThreadBody({
  helpers,
  disabled,
}: {
  helpers: UseChatHelpers<UIMessage>;
  disabled: boolean;
}) {
  const aui = useAui();
  return (
    <div className="flex min-h-[32rem] flex-col gap-4">
      <div aria-live="polite" className="flex flex-col gap-3" data-testid="admin-chat-stream">
        <ThreadPrimitive.Empty>
          <p className="text-body-md text-muted-foreground">
            No messages yet. Ask a question to start.
          </p>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages>
          {({ message }) => <MessageRow message={message} />}
        </ThreadPrimitive.Messages>
        <ThreadError />
      </div>
      <ChatComposer
        busy={helpers.status === "submitted" || helpers.status === "streaming"}
        inputRef={useComposerFocus(helpers)}
        onSend={(content) => void aui.thread.append(content)}
        onStop={() => aui.thread.cancelRun()}
        disabled={disabled}
      />
    </div>
  );
}

function MessageRow({ message }: { message: ThreadMessage & { isLast?: boolean } }) {
  if (message.role === "user") {
    return (
      <div data-testid={`chat-message-${message.id}`} data-role="user">
        <ChatMessageView role="user" content={textOf(message)} />
      </div>
    );
  }
  return (
    <div data-testid={`chat-message-${message.id}`} data-role="assistant">
      <MessagePrimitive.Parts
        components={{
          Text: ({ text }) => <ChatMessageView role="assistant" content={text} />,
          data: { by_name: { "deepfix-tool": ToolRow } },
        }}
      />
      {message.isLast ? (
        <ActionBarPrimitive.Root>
          <ActionBarPrimitive.Reload className="text-label-sm underline">
            Regenerate
          </ActionBarPrimitive.Reload>
        </ActionBarPrimitive.Root>
      ) : null}
    </div>
  );
}

function textOf(message: { content: readonly { type: string; text?: string }[] }): string {
  return message.content.map((part) => (part.type === "text" ? (part.text ?? "") : "")).join("");
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

/** Focus returns to the composer after a turn settles (FR-025). */
function useComposerFocus(helpers: UseChatHelpers<UIMessage>) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  const running = helpers.status === "submitted" || helpers.status === "streaming";
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) ref.current?.focus();
    wasRunning.current = running;
  }, [running]);
  return ref;
}
