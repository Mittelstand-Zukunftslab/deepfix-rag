import {
  createAskChatThread,
  deleteAskChatThread,
  getAskChatThread,
  listAskChatThreads,
  renameAskChatThread,
} from "@/client";

import type { AskThread, AskThreadDetail } from "@/schemas/ask-chat";
import { resumeTurn, streamTurn, type StreamHandlers } from "@/services/ask-chat-sse";

/**
 * The Ask chat's data layer: the five thread routes and the two stream calls.
 *
 * Every component above this module talks to it rather than to `fetch` or the
 * generated client directly, which is the same division `src/services/*` keeps
 * everywhere else. The two streaming calls delegate to `ask-chat-sse.ts`, the
 * one module allowed to read an event stream, so no component holds protocol
 * knowledge.
 */

export class AskChatError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AskChatError";
  }
}

function errorDetail(error: unknown, fallback: string): string {
  if (
    error &&
    typeof error === "object" &&
    "detail" in error &&
    typeof error.detail === "string" &&
    error.detail.length > 0
  ) {
    return error.detail;
  }
  return fallback;
}

export const askChatService = {
  async listThreads(): Promise<AskThread[]> {
    const result = await listAskChatThreads();
    if (result.error || !result.data) {
      throw new AskChatError(errorDetail(result.error, "Unable to load your conversations."));
    }
    return result.data;
  },

  async createThread(): Promise<AskThread> {
    const result = await createAskChatThread();
    if (result.error || !result.data) {
      throw new AskChatError(errorDetail(result.error, "Unable to start a conversation."));
    }
    return result.data;
  },

  async getThread(threadId: string): Promise<AskThreadDetail> {
    const result = await getAskChatThread({ path: { thread_id: threadId } });
    if (result.error || !result.data) {
      throw new AskChatError(errorDetail(result.error, "Unable to open that conversation."));
    }
    return result.data;
  },

  async renameThread(threadId: string, title: string): Promise<AskThread> {
    const result = await renameAskChatThread({
      path: { thread_id: threadId },
      body: { title },
    });
    if (result.error || !result.data) {
      throw new AskChatError(errorDetail(result.error, "Unable to rename that conversation."));
    }
    return result.data;
  },

  /**
   * Delete a conversation. A 404 means it is already gone, which is the outcome
   * the member wanted, so it is not reported as a failure.
   */
  async deleteThread(threadId: string): Promise<void> {
    const result = await deleteAskChatThread({ path: { thread_id: threadId } });
    if (result.error) {
      const status = (result.response as Response | undefined)?.status;
      if (status === 404) return;
      throw new AskChatError(errorDetail(result.error, "Unable to delete that conversation."));
    }
  },

  streamTurn,
  resumeTurn,
};

export type { StreamHandlers };
