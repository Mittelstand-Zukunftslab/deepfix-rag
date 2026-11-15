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
    turns:
      messages.length === 0
        ? []
        : [
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
    screen.getByRole("button", { name: "Send message" }).click();
  });
}

describe("the welcome state", () => {
  it("offers three chips on an empty thread and fills the composer without sending", async () => {
    THREADS["thread-empty"] = thread("thread-empty", "Empty", []);
    const { posts } = stubServer(() => sse([sseBody(turnParts(TURN, "Unused"))]));
    vi.spyOn(adminChatService, "threads").mockResolvedValue([summary("thread-empty", "Empty")]);
    render(<AdminChatPage chatEnabled={true} />);

    // Wait for the thread itself, so the chips below are the thread's own and not
    // the page's pre-thread welcome.
    await screen.findByRole("button", { name: "Empty" });
    const chips = await screen.findAllByRole("button", {
      name: /optimization run|champion configuration|recent runs/,
    });
    expect(chips.map((chip) => chip.textContent)).toEqual([
      "Start an optimization run",
      "Show the champion configuration",
      "Summarize the recent runs",
    ]);
    expect(posts).toHaveLength(0);

    await act(async () => {
      chips[1]!.click();
    });
    // A chip fills the composer; the admin still has to send it.
    const composer = screen.getByLabelText("Message the assistant") as HTMLTextAreaElement;
    await waitFor(() => expect(composer.value).toContain("champion configuration"));
    expect(posts).toHaveLength(0);
  });

  it("creates a thread from a chip on the pre-thread page without sending", async () => {
    const { posts } = stubServer(() => sse([sseBody(turnParts(TURN, "Unused"))]));
    vi.spyOn(adminChatService, "threads").mockResolvedValue([]);
    const create = vi
      .spyOn(adminChatService, "createThread")
      .mockResolvedValue({ thread_id: "thread-new", title: "", created_at: "", updated_at: "" });
    THREADS["thread-new"] = thread("thread-new", "New", []);
    render(<AdminChatPage chatEnabled={true} />);

    const chip = await screen.findByRole("button", { name: "Summarize the recent runs" });
    await act(async () => {
      chip.click();
    });
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Message the assistant") as HTMLTextAreaElement).value,
      ).toContain("recent optimization runs"),
    );
    expect(posts).toHaveLength(0);
  });

  it("is gone once the thread has a message", async () => {
    stubServer(() => sse([sseBody(turnParts(TURN, "Unused"))]));
    render(<AdminChatPage chatEnabled={true} />);
    await waitFor(() => expect(screen.getByText("Alpha answer")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Start an optimization run" })).toBeNull();
  });
});

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

  it("posts on Enter, leaves a Shift+Enter draft alone, and swaps Send for Stop", async () => {
    let release: () => void = () => undefined;
    const slow = new ReadableStream<Uint8Array>({
      start(controller) {
        release = () => {
          // The abort closes the stream first; that is the stop this case proves.
          try {
            controller.enqueue(new TextEncoder().encode(sseBody(turnParts(TURN, "Late"))));
            controller.close();
          } catch {
            // already closed by the cancel
          }
        };
      },
    });
    const { posts } = stubServer(
      () => new Response(slow, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    render(<AdminChatPage chatEnabled={true} />);
    await waitFor(() => expect(screen.getByText("Alpha answer")).toBeTruthy());

    const composer = screen.getByLabelText("Message the assistant") as HTMLTextAreaElement;
    await act(async () => {
      const setValue = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        "value",
      )!.set!;
      setValue.call(composer, "first line");
      composer.dispatchEvent(new Event("input", { bubbles: true }));
      composer.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(screen.queryByRole("button", { name: "Send message" })).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Send message" }) as HTMLButtonElement).disabled,
    ).toBe(false);
    // Shift+Enter is a newline, so nothing is posted.
    await act(async () => {
      composer.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", shiftKey: true, bubbles: true }),
      );
    });
    expect(posts).toHaveLength(0);

    // Enter sends, and the send control becomes Stop while the turn runs.
    await act(async () => {
      composer.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    await waitFor(() => expect(posts).toHaveLength(1));
    const stop = await screen.findByRole("button", { name: "Stop generating" });
    expect(screen.queryByRole("button", { name: "Send message" })).toBeNull();

    await act(async () => {
      stop.click();
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "Send message" })).toBeTruthy());
    await act(async () => {
      release();
      await new Promise((resolve) => setTimeout(resolve, 40));
    });
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
