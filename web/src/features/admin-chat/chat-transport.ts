import type { UIMessage } from "ai";
import { Chat } from "@ai-sdk/react";
import { AssistantChatTransport } from "@assistant-ui/react-ai-sdk";

import {
  chatToolProgress,
  chatTurnFrame,
  type ChatMessage,
  type ChatToolProgress,
  type ChatTurnFrame,
} from "@/schemas/admin-chat";
import { RESUME_PATH, STREAM_PATH } from "@/services/admin-chat-sse";

/**
 * The chat transport, its per-thread session, and the wire conversions.
 *
 * One `Chat` per thread is what keeps an in-flight reply bound to the thread it
 * started in: the AI SDK's chat holds the messages and the stream, so sharing a
 * single instance across threads (which `useChatRuntime` does, since its thread
 * list is in memory) is what made messages migrate between conversations.
 */

export type ChatHooks = {
  onTool: (progress: ChatToolProgress) => void;
  onTurn: (frame: ChatTurnFrame) => void;
  onError: (message: string) => void;
  onFinished: () => void;
};

export type ThreadChat = {
  chat: Chat<UIMessage>;
  /** The turn a reconnect should resume, set once the thread knows it. */
  resumeTurnId: { current: string | null };
};

/** The server's stored messages as AI SDK messages, oldest first. */
export function seedMessages(messages: ChatMessage[]): UIMessage[] {
  return messages
    .filter((item) => item.role === "user" || item.content.trim().length > 0)
    .map((item) => ({
      // An assistant message takes its turn id, which is the id a resume stream
      // opens with, so a replayed reply replaces the seeded one instead of
      // landing beside it as a second message.
      id: item.role === "assistant" && item.turn_id ? item.turn_id : item.message_id,
      role: item.role,
      parts: [{ type: "text" as const, text: item.content }],
    }));
}

/** The wire shape the backend validates a turn against. */
export function wireMessages(
  messages: UIMessage[],
): { role: "user" | "assistant"; content: string }[] {
  return messages.flatMap((item) => {
    if (item.role !== "user" && item.role !== "assistant") return [];
    const content = item.parts.map((part) => (part.type === "text" ? part.text : "")).join("");
    return [{ role: item.role, content }];
  });
}

/** A redacted sentence for an error the AI SDK surfaced. */
export function errorMessage(error: unknown): string {
  if (!(error instanceof Error)) return "Something went wrong. Try again.";
  // A 503 carries the chat's own sentence, inside the AI SDK's wrapper message.
  const detail = /"message"\s*:\s*"([^"]+)"/.exec(error.message);
  return detail?.[1] ?? "The reply stopped unexpectedly. Try again.";
}

export function createThreadChat(
  threadId: string,
  seed: UIMessage[],
  hooks: { current: ChatHooks },
): ThreadChat {
  const resumeTurnId: { current: string | null } = { current: null };
  const chat = new Chat<UIMessage>({
    id: threadId,
    messages: seed,
    transport: new AssistantChatTransport({
      api: STREAM_PATH,
      credentials: "include",
      prepareSendMessagesRequest: ({ messages, trigger }) => ({
        body: {
          thread_id: threadId,
          messages: wireMessages(messages),
          // The AI SDK's reload is this chat's regenerate: the backend replaces
          // the last answered turn instead of appending a second answer to it.
          regenerate: trigger === "regenerate-message",
          retry_of: null,
        },
      }),
      prepareReconnectToStreamRequest: () => {
        const turnId = resumeTurnId.current;
        if (!turnId) throw new Error("This thread has no reply to resume.");
        return { api: `${RESUME_PATH}/${encodeURIComponent(turnId)}/stream` };
      },
    }),
    onData: (part) => {
      if (part.type === "data-deepfix-turn") {
        const frame = chatTurnFrame.safeParse(part.data);
        if (frame.success) hooks.current.onTurn(frame.data);
        return;
      }
      if (part.type === "data-deepfix-tool") {
        const progress = chatToolProgress.safeParse(part.data);
        if (progress.success) hooks.current.onTool(progress.data);
      }
    },
    onFinish: () => hooks.current.onFinished(),
    onError: (error) => hooks.current.onError(errorMessage(error)),
  });
  return { chat, resumeTurnId };
}
