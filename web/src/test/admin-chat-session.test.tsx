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
 * exactly once. The `fetch` stub speaks the frames the backend actually emits
 * for a turn and for a resume, copied from a real streamed response.
 */

const TURN = "03fb365f57414be4b22a8e109472b69e";

function sse(lines: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(lines.join("")));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function sseBody(parts: unknown[]): string {
  return parts.map((part) => `data: ${JSON.stringify(part)}\n\n`).join("") + "data: [DONE]\n\n";
}

/** One turn, exactly as `POST /admin/chat/completions` streams it. */
function turnParts(turnId: string, text: string): unknown[] {
  return [
    { type: "start", messageId: turnId },
    { type: "text-start", id: `${turnId}-text` },
    { type: "text-delta", id: `${turnId}-text`, delta: text },
    { type: "data-deepfix-tool", data: { label: "Searching the web\u2026", state: "running" } },
    { type: "text-end", id: `${turnId}-text` },
    { type: "data-deepfix-turn", data: { turn_id: turnId, status: "completed", actions: [] } },
    { type: "finish", finishReason: "stop" },
  ];
}

/** A resume, exactly as `GET /admin/chat/turns/{id}/stream` replays it. */
function resumeParts(turnId: string, text: string): unknown[] {
  return [
    { type: "start", messageId: turnId },
    { type: "text-start", id: `${turnId}-text` },
    { type: "text-delta", id: `${turnId}-text`, delta: text },
    { type: "text-end", id: `${turnId}-text` },
    { type: "data-deepfix-turn", data: { turn_id: turnId, status: "completed", actions: [] } },
    { type: "finish", finishReason: "stop" },
  ];
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

function thread(
  threadId: string,
  title: string,
  messages: ChatMessageResponse[],
  turnStatus: "running" | "completed" = "completed",
): ChatThreadDetailResponse {
  return {
    thread: summary(threadId, title),
    messages,
    turns: [
      {
        turn_id: messages.at(-1)!.turn_id!,
        status: turnStatus,
        retry_of: null,
        error: null,
        first_token_ms: 0,
        started_at: "2026-11-15T00:00:00+00:00",
        ended_at: turnStatus === "completed" ? "2026-11-15T00:00:01+00:00" : null,
      },
    ],
    actions: [],
  };
}

const THREADS: Record<string, ChatThreadDetailResponse> = {
  "thread-a": thread("thread-a", "Alpha", [
    message("a-1", "user", "Alpha question", "turn-a"),
    message("a-2", "assistant", "Alpha answer", "turn-a"),
  ]),
  "thread-b": thread("thread-b", "Beta", [
    message("b-1", "user", "Beta question", "turn-b"),
    message("b-2", "assistant", "Beta answer", "turn-b"),
  ]),
};

/** Stub every route the page calls; the two streams are decided per test. */
function stubServer(
  send: () => Response,
  resume: (turnId: string) => Response = (turnId) => sse([sseBody(resumeParts(turnId, ""))]),
) {
  const posts: string[] = [];
  const resumes: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/admin/chat/completions")) {
        posts.push(String(init?.body ?? "{}"));
        return send();
      }
      if (url.includes("/stream")) {
        const turnId = decodeURIComponent(url.split("/turns/")[1]!.replace("/stream", ""));
        resumes.push(turnId);
        return resume(turnId);
      }
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
  vi.spyOn(adminChatService, "threads").mockResolvedValue([
    summary("thread-a", "Alpha"),
    summary("thread-b", "Beta"),
  ]);
  vi.spyOn(adminChatService, "thread").mockImplementation(async (threadId) => THREADS[threadId]!);
  vi.spyOn(adminChatService, "runs").mockResolvedValue([]);
  return { posts, resumes };
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

async function send(text: string) {
  const composer = screen.getByLabelText("Message the assistant") as HTMLTextAreaElement;
  await act(async () => {
    const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
    setValue.call(composer, text);
    composer.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    screen.getByRole("button", { name: "Send" }).click();
  });
}

describe("thread isolation", () => {
  it("keeps each thread's messages to itself across switches", async () => {
    stubServer(() => sse([sseBody(turnParts(TURN, "Fresh reply"))]));
    render(<AdminChatPage chatEnabled={true} />);
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
    const body = sseBody(turnParts(TURN, "Late reply"));
    let release: () => void = () => undefined;
    const slow = new ReadableStream<Uint8Array>({
      start(controller) {
        release = () => {
          controller.enqueue(new TextEncoder().encode(body));
          controller.close();
        };
      },
    });
    stubServer(
      () => new Response(slow, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    render(<AdminChatPage chatEnabled={true} />);
    await waitFor(() => expect(screen.getByText("Alpha answer")).toBeTruthy());
    await send("New question");
    // Switching away while the reply is still open must not move it.
    await openThread("Beta");
    await waitFor(() => expect(screen.getByText("Beta answer")).toBeTruthy());
    expect(screen.queryByText("Late reply")).toBeNull();
    await act(async () => {
      release();
      await new Promise((resolve) => setTimeout(resolve, 60));
    });
    expect(screen.queryByText("Late reply")).toBeNull();
    await openThread("Alpha");
    await waitFor(() => expect(screen.getByText("Late reply")).toBeTruthy());
    expect(screen.queryByText("Beta answer")).toBeNull();
  });
});

describe("one copy of every message", () => {
  it("draws a stored answer once, and never appends it again on a switch", async () => {
    stubServer(() => sse([sseBody(turnParts(TURN, "Fresh reply"))]));
    render(<AdminChatPage chatEnabled={true} />);
    await waitFor(() => expect(screen.getAllByText("Alpha answer")).toHaveLength(1));
    await openThread("Beta");
    await waitFor(() => expect(screen.getAllByText("Beta answer")).toHaveLength(1));
    await openThread("Alpha");
    await waitFor(() => expect(screen.getAllByText("Alpha answer")).toHaveLength(1));
  });

  it("shows a streamed reply once, with its step row and one regenerate", async () => {
    stubServer(() => sse([sseBody(turnParts(TURN, "Das ist eine Antwort."))]));
    render(<AdminChatPage chatEnabled={true} />);
    await waitFor(() => expect(screen.getByText("Alpha answer")).toBeTruthy());
    await send("Hallo");
    await waitFor(() => expect(screen.getAllByText("Das ist eine Antwort.")).toHaveLength(1));
    expect(screen.getAllByText("Searching the web\u2026")).toHaveLength(1);
    // Only the newest assistant message offers a regenerate control.
    expect(screen.getAllByRole("button", { name: "Regenerate" })).toHaveLength(1);
  });

  it("replays a running turn on reload without duplicating its text", async () => {
    // The page loads a thread whose reply was in flight, with the partial text
    // already stored: the resume replays the same text from the same turn id.
    THREADS["thread-reload"] = thread(
      "thread-reload",
      "Reload",
      [
        message("r-1", "user", "Hallo", TURN),
        message("r-2", "assistant", "Das ist eine Antwort.", TURN),
      ],
      "running",
    );
    const { resumes } = stubServer(
      () => sse([sseBody(turnParts(TURN, "unused"))]),
      (turnId) => sse([sseBody(resumeParts(turnId, "Das ist eine Antwort."))]),
    );
    vi.spyOn(adminChatService, "threads").mockResolvedValue([summary("thread-reload", "Reload")]);
    render(<AdminChatPage chatEnabled={true} />);
    await waitFor(() => expect(resumes).toEqual([TURN]));
    await waitFor(() => expect(screen.getAllByText("Das ist eine Antwort.")).toHaveLength(1));
    // The replay replaced the seeded message rather than landing beside it.
    expect(document.querySelectorAll("[data-role=assistant]")).toHaveLength(1);
  });

  it("seeds a stored answer under its turn id so a resume replaces it", () => {
    const seeded = seedMessages([
      message("m-1", "user", "Question", "turn-1"),
      message("m-2", "assistant", "Answer", "turn-1"),
    ]);
    expect(seeded.map((item) => item.id)).toEqual(["m-1", "turn-1"]);
    expect(wireMessages(seeded)).toEqual([
      { role: "user", content: "Question" },
      { role: "assistant", content: "Answer" },
    ]);
  });
});

describe("regenerate", () => {
  it("asks the server to replace the last answer rather than append one", async () => {
    const { posts } = stubServer(() => sse([sseBody(turnParts(TURN, "Second answer"))]));
    render(<AdminChatPage chatEnabled={true} />);
    await waitFor(() => expect(screen.getByText("Alpha answer")).toBeTruthy());
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
    expect(document.querySelectorAll("[data-role=assistant]")).toHaveLength(1);
  });
});
