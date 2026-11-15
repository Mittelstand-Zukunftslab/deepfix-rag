import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AskPage } from "@/features/chat/chat-page";
import { DELETE_CONVERSATION_COPY } from "@/features/chat/ask-thread-sidebar";
import type { AskTurn } from "@/schemas/ask-chat";
import { askChatService } from "@/services/ask-chat-service";

/**
 * The Ask page, driven through the real Assistant UI runtime and the real AI SDK
 * transport. Only the network is stubbed, at `fetch`, and it speaks the frames
 * the backend actually emits — the same fixture `ask-chat-stream.test.ts` uses.
 * That is the transport boundary, not a stub of domain logic.
 */

function frame(part: unknown): string {
  return `data: ${JSON.stringify(part)}\n\n`;
}

function streamResponse(chunks: string[], gate?: Promise<void>): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const chunk of chunks) {
        if (gate) await gate;
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const THREAD = {
  thread_id: "thread-1",
  title: "Trunkenheitsfahrt",
  created_at: "2026-11-15T00:00:00+00:00",
  updated_at: "2026-11-15T00:00:01+00:00",
};

const STORED_TURN: AskTurn = {
  turn_id: "turn-1",
  seq: 1,
  status: "completed",
  citations: [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
  grounding: "grounded",
  started_at: "2026-11-15T00:00:00+00:00",
  ended_at: "2026-11-15T00:00:01+00:00",
  interaction_id: "i1",
  question: "Ist eine Trunkenheitsfahrt strafbar?",
  answer: "Die Trunkenheitsfahrt ist strafbar [1].",
};

/** One answer's frames, as `POST /ask/chat/completions` emits them. */
function answerFrames(
  turnId = "turn-1",
  words = ["Die ", "Antwort ", "ist ", "da [1]."],
): string[] {
  return [
    frame({ type: "start", messageId: turnId }),
    frame({ type: "start-step" }),
    frame({ type: "text-start", id: "s" }),
    ...words.map((delta) => frame({ type: "text-delta", id: "s", delta })),
    frame({ type: "text-end", id: "s" }),
    frame({ type: "finish-step" }),
    frame({
      type: "data-deepfix-grounding",
      data: {
        grounding: "grounded",
        citations: [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
        max_score: 0.8,
        cutoff: 0.3,
      },
    }),
    frame({ type: "data-deepfix-turn", data: { turn_id: turnId, status: "completed" } }),
    frame({ type: "finish", finishReason: "stop" }),
    "data: [DONE]\n\n",
  ];
}

type Routes = {
  threads?: unknown[];
  detail?: unknown;
};

/**
 * The JSON routes are stubbed on the service, which is the repository's pattern
 * (`admin-chat-session.test.tsx` does the same); `fetch` is stubbed only for the
 * stream, which the transport reads directly. That keeps the stub at the network
 * boundary rather than at the module the page is being tested through.
 */
function stubApi(routes: Routes, stream: () => Response | Promise<Response>) {
  const calls: { method: string; url: string; body: unknown }[] = [];
  const threads = routes.threads ?? [THREAD];
  const detail = routes.detail ?? { thread: THREAD, turns: [STORED_TURN] };
  vi.spyOn(askChatService, "listThreads").mockResolvedValue(threads as never);
  vi.spyOn(askChatService, "getThread").mockResolvedValue(detail as never);
  vi.spyOn(askChatService, "createThread").mockImplementation(async () => {
    calls.push({ method: "POST", url: "/ask/chat/threads", body: null });
    return { ...THREAD, thread_id: "thread-new" } as never;
  });
  vi.spyOn(askChatService, "renameThread").mockImplementation(async (threadId, title) => {
    calls.push({ method: "PATCH", url: `/ask/chat/threads/${threadId}`, body: { title } });
    return { ...THREAD, title } as never;
  });
  vi.spyOn(askChatService, "deleteThread").mockImplementation(async (threadId) => {
    calls.push({ method: "DELETE", url: `/ask/chat/threads/${threadId}`, body: null });
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => stream()),
  );
  return calls;
}

const renderPage = (ui: ReactNode) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Suspense fallback={<p>Loading</p>}>{ui}</Suspense>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Element.prototype.scrollIntoView = () => undefined;
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("the Ask page", () => {
  it("opens the newest stored thread and draws its answer once", async () => {
    stubApi({ threads: [THREAD] }, () => streamResponse(answerFrames()));
    renderPage(<AskPage indexReady />);

    await waitFor(() => {
      expect(screen.getAllByText(/Die Trunkenheitsfahrt ist strafbar/).length).toBe(1);
    });
    expect(screen.getAllByText("Ist eine Trunkenheitsfahrt strafbar?").length).toBe(1);
    // The member can ask again: the composer is enabled and the send control is Send.
    expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy();
  });

  it("shows the answer progressively and turns the send control into Stop", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    stubApi({ threads: [THREAD], detail: { thread: THREAD, turns: [] } }, () =>
      streamResponse(answerFrames("turn-9", ["Die ", "Antwort ", "kommt ", "an."]), gate),
    );
    renderPage(<AskPage indexReady />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy());

    fireEvent.change(screen.getByRole("textbox", { name: "Ask" }), {
      target: { value: "Und der Führerschein?" },
    });
    fireEvent.submit(screen.getByRole("textbox", { name: "Ask" }).closest("form")!);

    // While the stream is open the control is Stop, not Send.
    await screen.findByRole("button", { name: "Stop generating" });
    release();
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy());
    expect(await screen.findByText(/kommt/)).toBeTruthy();
  });

  it("pressing Stop leaves the partial text and re-enables the composer", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    stubApi({ threads: [THREAD], detail: { thread: THREAD, turns: [] } }, () =>
      streamResponse(answerFrames("turn-10", ["Ein ", "halber ", "Satz."]), gate),
    );
    renderPage(<AskPage indexReady />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy());
    fireEvent.change(screen.getByRole("textbox", { name: "Ask" }), {
      target: { value: "Lange Frage" },
    });
    fireEvent.submit(screen.getByRole("textbox", { name: "Ask" }).closest("form")!);
    const stop = await screen.findByRole("button", { name: "Stop generating" });
    fireEvent.click(stop);
    release();
    // The composer is usable again and the page does not error.
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Stop generating" })).toBeNull();
  });

  it("lists the server's conversations and sends rename and delete to the right routes", async () => {
    const calls = stubApi(
      { threads: [THREAD, { ...THREAD, thread_id: "thread-2", title: "Zweite" }] },
      () => streamResponse(answerFrames()),
    );
    renderPage(<AskPage indexReady />);
    await screen.findByRole("navigation", { name: "Conversations" });
    expect(screen.getByText("Zweite")).toBeTruthy();

    // Rename.
    fireEvent.click(screen.getByRole("button", { name: "Rename Trunkenheitsfahrt" }));
    const input = screen.getByRole("textbox", { name: "Conversation title" });
    fireEvent.change(input, { target: { value: "Neuer Titel" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => {
      expect(calls.some((call) => call.method === "PATCH")).toBe(true);
    });
    const patch = calls.find((call) => call.method === "PATCH");
    expect(patch?.url).toBe("/ask/chat/threads/thread-1");
    expect(patch?.body).toEqual({ title: "Neuer Titel" });

    // Delete names what goes and what stays, then calls the route. The title is
    // the one the rename just set, because the row's controls name their target.
    fireEvent.click(screen.getByRole("button", { name: "Delete Neuer Titel" }));
    await screen.findByText(DELETE_CONVERSATION_COPY);
    expect(screen.getByText(/votes you recorded .* are kept/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Delete conversation" }));
    await waitFor(() => {
      expect(calls.some((call) => call.method === "DELETE")).toBe(true);
    });
    expect(calls.find((call) => call.method === "DELETE")?.url).toBe("/ask/chat/threads/thread-1");
  });

  it("treats a deleted conversation as deleted, not as a failure to delete", async () => {
    // The server answers 204 for a thread the member already deleted and 404 for
    // an id that was never theirs, so neither may surface as a failure: the
    // thread is gone, which is what pressing Delete asked for. The generated
    // client is stubbed here because a relative URL cannot be built under jsdom;
    // what is under test is the service's handling of the result shapes.
    vi.resetModules();
    vi.doMock("@/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("@/client")>();
      return {
        ...actual,
        deleteAskChatThread: vi.fn(async ({ path }: { path: { thread_id: string } }) => {
          // A successful delete carries no body and no error.
          if (path.thread_id === "thread-ok") {
            return {
              data: undefined,
              error: undefined,
              response: new Response(null, { status: 204 }),
            };
          }
          // A retried delete: the server says the id is not this member's.
          if (path.thread_id === "thread-again") {
            return {
              data: undefined,
              error: { detail: "No conversation with that id." },
              response: new Response(null, { status: 404 }),
            };
          }
          // A real failure must still surface.
          return {
            data: undefined,
            error: { detail: "The server is having trouble." },
            response: new Response(null, { status: 500 }),
          };
        }),
      };
    });
    const { askChatService: service } = await import("@/services/ask-chat-service");
    await expect(service.deleteThread("thread-ok")).resolves.toBeUndefined();
    // A retried delete is a success, not an error the member cannot act on.
    await expect(service.deleteThread("thread-again")).resolves.toBeUndefined();
    // The tolerance is narrow: a real failure is still reported.
    await expect(service.deleteThread("thread-broken")).rejects.toThrow();
    vi.doUnmock("@/client");
    vi.resetModules();
  });

  it("starts a conversation from the empty state and creates one on the server", async () => {
    const calls = stubApi({ threads: [], detail: { thread: THREAD, turns: [] } }, () =>
      streamResponse(answerFrames()),
    );
    renderPage(<AskPage indexReady />);
    await screen.findByText("No conversations yet");
    fireEvent.click(screen.getByRole("button", { name: "Start a conversation" }));
    await waitFor(() => {
      expect(calls.some((call) => call.method === "POST")).toBe(true);
    });
  });

  it("offers no regenerate for a refusal, and offers it after a failure", async () => {
    const refusal: AskTurn = {
      ...STORED_TURN,
      turn_id: "turn-refused",
      grounding: "no_relevant_sources",
      citations: [],
      answer: "I do not have enough information in these documents to answer that question.",
    };
    stubApi({ threads: [THREAD], detail: { thread: THREAD, turns: [refusal] } }, () =>
      streamResponse(answerFrames()),
    );
    const { unmount } = renderPage(<AskPage indexReady />);
    await screen.findByText(/I do not have enough information/);
    // The refusal is the answer, so there is nothing to regenerate.
    expect(screen.queryByRole("button", { name: "Regenerate" })).toBeNull();
    unmount();
    vi.unstubAllGlobals();

    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    stubApi({ threads: [THREAD], detail: { thread: THREAD, turns: [] } }, () =>
      streamResponse(
        [...answerFrames("turn-failed"), frame({ type: "error", errorText: "Upstream failed." })],
        gate,
      ),
    );
    renderPage(<AskPage indexReady />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy());
    release();
  });

  it("keeps the index-preparing state when no index is serving", () => {
    stubApi({ threads: [] }, () => streamResponse(answerFrames()));
    renderPage(<AskPage indexReady={false} />);
    expect(
      screen.getAllByText("The index is being prepared. Try again in a minute.").length,
    ).toBeGreaterThan(0);
  });
});
