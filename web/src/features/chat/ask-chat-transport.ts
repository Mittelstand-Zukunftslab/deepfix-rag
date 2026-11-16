import type { UIMessage } from "ai";
import { Chat } from "@ai-sdk/react";
import { AssistantChatTransport } from "@assistant-ui/react-ai-sdk";

import {
  askGroundingFrame,
  askStatusFrame,
  askTurnFrame,
  type AskGroundingFrame,
  type AskTurn,
  type AskTurnFrame,
} from "@/schemas/ask-chat";
import { RESUME_PATH, STREAM_PATH } from "@/services/ask-chat-sse";

/**
 * The Ask chat's transport: one `Chat` per thread.
 *
 * One `Chat` per thread is what keeps an in-flight answer bound to the thread it
 * started in. The AI SDK's `Chat` owns its messages and its stream, so a reply
 * streaming in one thread cannot appear in another — the bug CHAT-015 fixed in
 * the admin chat by the same means, and the same bug ASK-006 fixed for pending
 * state in the form the Ask page used to be.
 *
 * The shape follows `src/features/admin-chat/chat-transport.ts` deliberately: it
 * is the proven arrangement, not a second invention.
 */

export type AskChatHooks = {
  onGrounding: (frame: AskGroundingFrame) => void;
  onTurn: (frame: AskTurnFrame) => void;
  /** A non-terminal status, e.g. `reasoning` while the model thinks (D-12). */
  onStatus?: (status: string) => void;
  onError: (message: string) => void;
  onFinished: () => void;
};

export type AskThreadChat = {
  chat: Chat<UIMessage>;
  /** The turn a reconnect should resume, set once the thread knows it. */
  resumeTurnId: { current: string | null };
};

/** The thread's stored turns as AI SDK messages: one user, one assistant, in order. */
export function seedMessages(turns: AskTurn[]): UIMessage[] {
  return turns.flatMap((turn) => {
    const messages: UIMessage[] = [];
    if (turn.question) {
      messages.push({
        id: `${turn.turn_id}-question`,
        role: "user",
        parts: [{ type: "text" as const, text: turn.question }],
      });
    }
    if (turn.answer && turn.answer.trim().length > 0) {
      messages.push({
        // An assistant message takes its turn id, which is the id a resume stream
        // opens with, so a replayed answer replaces the seeded one instead of
        // landing beside it as a second message.
        id: turn.turn_id,
        role: "assistant",
        parts: [{ type: "text" as const, text: turn.answer }],
      });
    }
    return messages;
  });
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

/**
 * A sentence for an error the AI SDK surfaced.
 *
 * The API writes the sentence a member should see — "You have used 20 of 20
 * questions…", "The index is being prepared…" — and wraps it in JSON inside the
 * AI SDK's `Error` message. Pulling it out is what makes a 429 or a 503 reach the
 * member as the API wrote it rather than as a generic failure.
 */
export function errorMessage(error: unknown): string {
  if (!(error instanceof Error)) return "Something went wrong. Try again.";
  const detail = /"detail"\s*:\s*"([^"]+)"/.exec(error.message);
  return detail?.[1] ?? "The answer stopped unexpectedly. Try again.";
}

export function createAskChat(
  threadId: string,
  seed: UIMessage[],
  hooks: { current: AskChatHooks },
): AskThreadChat {
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
          // the newest answered turn instead of appending a second answer to it.
          regenerate: trigger === "regenerate-message",
        },
      }),
      prepareReconnectToStreamRequest: () => {
        const turnId = resumeTurnId.current;
        if (!turnId) throw new Error("This conversation has no answer to resume.");
        return { api: `${RESUME_PATH}/${encodeURIComponent(turnId)}/stream` };
      },
    }),
    onData: (part) => {
      if (part.type === "data-deepfix-turn") {
        const frame = askTurnFrame.safeParse(part.data);
        if (frame.success) {
          if (frame.data.turn_id) resumeTurnId.current = frame.data.turn_id;
          hooks.current.onTurn(frame.data);
        }
        return;
      }
      if (part.type === "data-deepfix-grounding") {
        const frame = askGroundingFrame.safeParse(part.data);
        if (frame.success) hooks.current.onGrounding(frame.data);
        return;
      }
      if (part.type === "data-deepfix-status") {
        // The model is reasoning but has not written a word yet. The page shows
        // this so a long silent step does not look like a hung request (D-12).
        const frame = askStatusFrame.safeParse(part.data);
        if (frame.success) hooks.current.onStatus?.(frame.data.status);
      }
    },
    onFinish: () => hooks.current.onFinished(),
    onError: (error) => hooks.current.onError(errorMessage(error)),
  });
  return { chat, resumeTurnId };
}
