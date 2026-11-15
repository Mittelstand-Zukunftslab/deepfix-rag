import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { seedMessages, wireMessages } from "@/features/admin-chat/chat-transport";
import type {
  ChatMessageResponse,
  ChatThreadDetailResponse,
  ChatThreadSummaryResponse,
} from "@/client";
import { adminChatService } from "@/services/admin-chat-service";
import { AdminChatPage } from "@/features/admin-chat/admin-chat-page";

/**
 * The admin chat against a stubbed server.
 *
 * These drive the real Assistant UI runtime and the real AI SDK transport, so
 * what they prove is the wiring the bugs lived in: which runtime owns which
 * thread's messages, what a reload posts, and that a stored answer is drawn
 * exactly once. The `fetch` stub speaks the same SSE stream the backend emits.
 */

function frame(part: unknown): string {
  return `data: ${JSON.stringify(part)}\n\n`;
}

function replyParts(text: string, turnId: string): unknown[] {
  return [
    { type: "start", messageId: turnId },
    { type: "text-start", id: `${turnId}-text` },
    { type: "text-delta", id: `${turnId}-text`, delta: text },
    { type: "text-end", id: `${turnId}-text` },
    { type: "data-deepfix-turn", data: { turn_id: turnId, status: "completed", actions: [] } },
    { type: "finish", finishReason: "stop" },
  ];
}

function sseBody(parts: unknown[]): string {
  return parts.map(frame).join("") + "data: [DONE]\n\n";
}

function reply(text: string, turnId: string): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(sseBody(replyParts(text, turnId))));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function message(
  id: string,
  role: "user" | "assistant",
  content: string,
  turnId: string,
): ChatMessageResponse {
  return {
    message_id: id,
    turn_id: turnId,
    role,
    seq: 1,
    content,
    created_at: "2026-11-15T00:00:00+00:00",
  };
}

function summary(threadId: string, title: string): ChatThreadSummaryResponse {
  return {
    thread_id: threadId,
    title,
    created_at: "2026-11-15T00:00:00+00:00",
    updated_at: "2026-11-15T00:00:00+00:00",
    last_turn_status: "completed",
  };
}

const THREADS: Record<string, ChatThreadDetailResponse> = {
  "thread-a": {
    thread: summary("thread-a", "Alpha"),
    messages: [
      message("a-1", "user", "Alpha question", "turn-a"),
      message("a-2", "assistant", "Alpha answer", "turn-a"),
    ],
    turns: [],
    actions: [],
  },
  "thread-b": {
    thread: summary("thread-b", "Beta"),
    messages: [
      message("b-1", "user", "Beta question", "turn-b"),
      message("b-2", "assistant", "Beta answer", "turn-b"),
    ],
    turns: [],
    actions: [],
  },
};

/** The stub server; `send` decides what a completions call answers with. */
function stubServer(send: () => Response) {
  const posts: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/admin/chat/completions")) {
        posts.push(String(init?.body ?? "{}"));
        return send();
      }
      return reply("", "resumed");
    }),
  );
  vi.spyOn(adminChatService, "threads").mockResolvedValue([
    summary("thread-a", "Alpha"),
    summary("thread-b", "Beta"),
  ]);
  vi.spyOn(adminChatService, "thread").mockImplementation(async (threadId) => THREADS[threadId]!);
  vi.spyOn(adminChatService, "runs").mockResolvedValue([]);
  return { posts };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function openThread(title: string) {
  const button = await screen.findByRole("button", { name: title });
  await act(async () => {
    button.click();
  });
}

async function type(text: string) {
  const composer = screen.getByLabelText("Message the assistant") as HTMLTextAreaElement;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
    setter.call(composer, text);
    composer.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    screen.getByRole("button", { name: "Send" }).click();
  });
}

describe("thread isolation", () => {
  it("keeps each thread's messages to itself across switches", async () => {
    stubServer(() => reply("Fresh reply", "turn-new"));
    render(<AdminChatPage chatEnabled={true} />);
    // The first thread in the list opens first.
    await waitFor(() => expect(screen.getByText("Alpha answer")).toBeTruthy());
    expect(screen.queryByText("Beta answer")).toBeNull();
    await openThread("Beta");
    await waitFor(() => expect(screen.getByText("Beta answer")).toBeTruthy());
    expect(screen.queryByText("Alpha answer")).toBeNull();
    await openThread("Alpha");
    await waitFor(() => expect(screen.getByText("Alpha answer")).toBeTruthy());
    expect(screen.queryByText("Beta answer")).toBeNull();
  });

  it("binds an in-flight reply to the thread it started in", async () => {
    let release: () => void = () => undefined;
    const slow = new ReadableStream<Uint8Array>({
      start(controller) {
        release = () => {
          controller.enqueue(new TextEncoder().encode(sseBody(replyParts("Late reply", "late"))));
          controller.close();
        };
      },
    });
    stubServer(
      () =>
        new Response(slow, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
    );

    render(<AdminChatPage chatEnabled={true} />);
    await waitFor(() => expect(screen.getByText("Alpha answer")).toBeTruthy());
    await type("New question");
    // Switching away while the reply is still open must not move it.
    await openThread("Beta");
    await waitFor(() => expect(screen.getByText("Beta answer")).toBeTruthy());
    expect(screen.queryByText("Late reply")).toBeNull();
    await act(async () => {
      release();
    });
    expect(screen.queryByText("Late reply")).toBeNull();
    await openThread("Alpha");
    await waitFor(() => expect(screen.getByText("Late reply")).toBeTruthy());
    expect(screen.queryByText("Beta answer")).toBeNull();
  });
});

describe("one copy of every message", () => {
  it("draws a stored answer once, and never appends it again on a switch", async () => {
    stubServer(() => reply("Fresh reply", "turn-new"));
    render(<AdminChatPage chatEnabled={true} />);
    await waitFor(() => expect(screen.getAllByText("Alpha answer")).toHaveLength(1));
    await openThread("Beta");
    await waitFor(() => expect(screen.getAllByText("Beta answer")).toHaveLength(1));
    await openThread("Alpha");
    await waitFor(() => expect(screen.getAllByText("Alpha answer")).toHaveLength(1));
  });

  it("seeds a stored answer under its turn id so a resume replaces it", () => {
    const seeded = seedMessages([
      message("m-1", "user", "Question", "turn-1"),
      message("m-2", "assistant", "Answer", "turn-1"),
    ]);
    // The assistant message is keyed by its turn, which is the id the resume
    // stream opens with; the user message keeps its own id.
    expect(seeded.map((item) => item.id)).toEqual(["m-1", "turn-1"]);
    expect(wireMessages(seeded)).toEqual([
      { role: "user", content: "Question" },
      { role: "assistant", content: "Answer" },
    ]);
  });
});

describe("regenerate", () => {
  it("asks the server to replace the last answer rather than append one", async () => {
    const { posts } = stubServer(() => reply("Second answer", "turn-b"));
    render(<AdminChatPage chatEnabled={true} />);
    await waitFor(() => expect(screen.getByText("Alpha answer")).toBeTruthy());
    // Only the last assistant message offers a regenerate control.
    const regenerate = screen.getAllByRole("button", { name: "Regenerate" });
    expect(regenerate).toHaveLength(1);
    await act(async () => {
      regenerate[0]!.click();
    });
    await waitFor(() => expect(posts.length).toBe(1));
    const body = JSON.parse(posts[0]!) as { regenerate: boolean; messages: unknown[] };
    expect(body.regenerate).toBe(true);
    // The reload drops the answer it replaces, exactly as the AI SDK does; the
    // server resolves which turn that was from the thread.
    expect(body.messages).toEqual([{ role: "user", content: "Alpha question" }]);
    await waitFor(() => expect(screen.getByText("Second answer")).toBeTruthy());
    expect(screen.queryByText("Alpha answer")).toBeNull();
  });
});
