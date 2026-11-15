import type {
  ChatActionResponse,
  ChatThreadDetailResponse,
  ChatThreadResponse,
  ChatThreadRunResponse,
  ChatThreadSummaryResponse,
  OptimizationRunResponse,
} from "@/client";
import {
  confirmChatAction,
  createChatThread,
  deleteChatThread,
  fetchChatRuns,
  fetchChatThread,
  fetchChatThreads,
  linkChatRun,
  rejectChatAction,
} from "@/services/admin-chat.functions";
import {
  resumeTurn as resumeStream,
  streamTurn as streamRequest,
  type StreamHandlers,
} from "@/services/admin-chat-sse";

export type { StreamHandlers };

export class AdminChatRequestError extends Error {
  /** The stable `code` from a chat 503, when the body carried one. */
  readonly code?: string;

  constructor(message: string, code?: string) {
    super(message);
    this.name = "AdminChatRequestError";
    this.code = code;
  }
}

async function call<T>(request: () => Promise<T>): Promise<T> {
  try {
    return await request();
  } catch (error) {
    const message =
      error instanceof Error && error.message
        ? error.message
        : "Unable to reach the server. Try again.";
    throw new AdminChatRequestError(message);
  }
}

/**
 * The admin chat's data layer.
 *
 * Every JSON route goes through an admin-checked server function, so a member
 * cannot reach one. The two streaming calls go through `admin-chat-sse.ts`,
 * which posts to the same-origin `/api/admin/chat/...` path the web server
 * already proxies to FastAPI. No model id, key, or Hermes host is named here.
 */
export const adminChatService = {
  threads(): Promise<ChatThreadSummaryResponse[]> {
    return call(() => fetchChatThreads());
  },

  thread(threadId: string): Promise<ChatThreadDetailResponse> {
    return call(() => fetchChatThread({ data: { threadId } }));
  },

  createThread(): Promise<ChatThreadResponse> {
    return call(() => createChatThread());
  },

  deleteThread(threadId: string): Promise<{ deleted: true }> {
    return call(() => deleteChatThread({ data: { threadId } }));
  },

  streamTurn(
    input: {
      threadId: string;
      messages: { role: "user" | "assistant"; content: string }[];
      retryOf?: string | null;
      signal?: AbortSignal;
    },
    handlers: StreamHandlers,
  ): Promise<void> {
    return streamRequest(input, handlers);
  },

  resumeTurn(turnId: string, handlers: StreamHandlers, signal?: AbortSignal): Promise<void> {
    return resumeStream(turnId, handlers, signal);
  },

  confirmAction(actionId: string): Promise<OptimizationRunResponse> {
    return call(() => confirmChatAction({ data: { actionId } }));
  },

  rejectAction(actionId: string): Promise<ChatActionResponse> {
    return call(() => rejectChatAction({ data: { actionId } }));
  },

  linkRun(threadId: string, runId: string): Promise<ChatThreadRunResponse> {
    return call(() => linkChatRun({ data: { threadId, runId } }));
  },

  runs(threadId: string): Promise<ChatThreadRunResponse[]> {
    return call(() => fetchChatRuns({ data: { threadId } }));
  },
};
