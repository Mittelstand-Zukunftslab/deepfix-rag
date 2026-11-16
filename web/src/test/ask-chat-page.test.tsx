import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/app-shell";
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

/**
 * A stream that sends its first `before` chunks, then waits for the gate, then the
 * rest. It is what a real turn looks like while the model is still writing, and it
 * is how a test observes the state in between — a gate on every chunk would hold
 * the whole stream before anything reached the page.
 */
function streamGatedAt(chunks: string[], before: number, gate: Promise<void>): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      chunks.slice(0, before).forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      await gate;
      chunks.slice(before).forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

/**
 * A stream that keeps writing after its first words and never closes — what a
 * provider looks like while it is still answering. Stop has to cancel that from
 * the client side, which is the case the reported bug happened in.
 */
function openStream(words: string[], turnId: string): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const opening = [
        frame({ type: "start", messageId: turnId }),
        frame({ type: "start-step" }),
        frame({ type: "text-start", id: "s" }),
        ...words.map((delta) => frame({ type: "text-delta", id: "s", delta })),
      ];
      for (const chunk of opening) controller.enqueue(encoder.encode(chunk));
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

/**
 * A stream that reports a `reasoning` status and then stays open with its text
 * group unfinished — what a stopped mid-reasoning turn leaves behind.
 */
function openStreamWithStatus(words: string[], turnId: string): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of [
          frame({ type: "start", messageId: turnId }),
          frame({ type: "start-step" }),
          frame({ type: "data-deepfix-status", data: { status: "reasoning" } }),
          frame({ type: "text-start", id: "s" }),
          ...words.map((delta) => frame({ type: "text-delta", id: "s", delta })),
        ]) {
          controller.enqueue(encoder.encode(chunk));
        }
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
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

const PASSAGES = [
  {
    rank: 1,
    doc_id: "doc_de_001",
    filename: "stgb_315c.txt",
    text: "Gemäß § 315c StGB wird die Trunkenheitsfahrt bestraft.",
    score: 0.83,
    truncated: false,
  },
];

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

  it("pressing Stop keeps the partial answer and the sources it had found", async () => {
    // A real provider is still writing when Stop lands, so the stream never
    // closes on its own — which is what leaves the answer's text part `streaming`
    // and, before the fix, blanked the message and its evidence from the screen.
    const stoppedTurn = {
      ...STORED_TURN,
      turn_id: "turn-10",
      status: "cancelled" as const,
      // Deliberately not what the stream carried: the assertion below proves the
      // text came from the store, which is the path Stop takes.
      answer: "Ein halber Satz aus dem Speicher.",
      grounding: "grounded" as const,
      citations: [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
      passages: PASSAGES,
      retrieval_query: "Trunkenheitsfahrt",
    };
    stubApi({ threads: [THREAD], detail: { thread: THREAD, turns: [] } }, () =>
      openStream(["Ein ", "halber ", "Satz."], "turn-10"),
    );
    const cancelled: string[] = [];
    vi.spyOn(askChatService, "cancelTurn").mockImplementation(async (turnId) => {
      cancelled.push(turnId);
    });
    // The first read is the empty thread the page opens; after the stop the store
    // holds the cancelled turn with its partial text and passages, and that is what
    // the page re-reads.
    let reads = 0;
    vi.spyOn(askChatService, "getThread").mockImplementation(async () => {
      reads += 1;
      return (
        reads === 1 ? { thread: THREAD, turns: [] } : { thread: THREAD, turns: [stoppedTurn] }
      ) as never;
    });
    renderPage(<AskPage indexReady />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy());
    fireEvent.change(screen.getByRole("textbox", { name: "Ask" }), {
      target: { value: "Lange Frage" },
    });
    fireEvent.submit(screen.getByRole("textbox", { name: "Ask" }).closest("form")!);
    // The stop lands mid-answer — the reported case, not a stop before anything
    // arrived.
    await screen.findByText(/Ein/);
    const stop = await screen.findByRole("button", { name: "Stop generating" });
    fireEvent.click(stop);
    // Stop tells the server, explicitly. A closed socket is what a reload does,
    // and the server must not treat the two alike, so the intent is a request.
    await waitFor(() => {
      expect(cancelled).toContain("turn-10");
    });
    // The composer is usable again and the page does not error.
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Stop generating" })).toBeNull();
    // The answer the member had read is on screen from the store, and so is its
    // evidence: the sources panel is rebuilt from the turn's passages.
    await waitFor(() => {
      expect(screen.getByText(/aus dem Speicher/)).toBeTruthy();
    });
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    // A cancelled turn that searched is grounded, not "Partly grounded" for an
    // empty view: the badge says what the turn reached.
    expect(screen.queryByText("Partly grounded")).toBeNull();
  });

  it("calls a thanks after a grounded answer reused evidence, not partly grounded", async () => {
    // The reported thread: a grounded answer, then "Thanks!". The second turn
    // searched nothing and cited nothing, so nothing about it is missing — but the
    // first answer's passages are still on screen, and the thread-wide passages
    // alone cannot tell the two turns apart.
    const firstTurn = {
      ...STORED_TURN,
      turn_id: "turn-1",
      question: "Ist eine Trunkenheitsfahrt strafbar?",
      answer: "Die Trunkenheitsfahrt ist strafbar [1].",
      citations: [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
      passages: PASSAGES,
      retrieval_query: "Trunkenheitsfahrt strafbar",
    };
    const thanks = {
      ...STORED_TURN,
      turn_id: "turn-2",
      seq: 2,
      question: "Thanks!",
      answer: "Gern geschehen.",
      grounding: "grounded" as const,
      citations: [],
      passages: [],
      retrieval_query: null,
      interaction_id: "i2",
    };
    stubApi({ threads: [THREAD], detail: { thread: THREAD, turns: [firstTurn, thanks] } }, () =>
      streamResponse(answerFrames()),
    );
    renderPage(<AskPage indexReady />);
    await waitFor(() => {
      expect(screen.getByText("Reused earlier evidence")).toBeTruthy();
    });
    expect(screen.queryByText("Partly grounded")).toBeNull();
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

describe("what a reloaded or stopped answer shows", () => {
  it("keeps a stored answer's citations clickable after a reload", async () => {
    // A stored answer carries its own citations. Handing every message the newest
    // verdict made an older answer's markers render as raw glyphs — the box, the
    // number, another box — with no control to press.
    const firstTurn = {
      ...STORED_TURN,
      turn_id: "turn-1",
      question: "Erste Frage",
      answer: "Die erste Antwort [1].",
      citations: [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
      passages: PASSAGES,
    };
    const secondTurn = {
      ...STORED_TURN,
      turn_id: "turn-2",
      seq: 2,
      question: "Zweite Frage",
      answer: "Die zweite Antwort [2].",
      citations: [{ marker: 2, rank: 2, doc_id: "doc_de_002" }],
      passages: PASSAGES,
    };
    stubApi({ threads: [THREAD], detail: { thread: THREAD, turns: [firstTurn, secondTurn] } }, () =>
      streamResponse(answerFrames()),
    );
    renderPage(<AskPage indexReady />);

    // Both stored answers draw a citation control of their own — not just the last.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Show passage 1" })).toBeTruthy();
      expect(screen.getByRole("button", { name: "Show passage 2" })).toBeTruthy();
    });
    // And no marker is left as a raw private-use glyph.
    expect(document.body.textContent).not.toMatch(/[\uE000\uE001]/);
  });

  it("resumes a running turn on reload and shows the rest of its answer", async () => {
    // The reported bug: ask a question, reload while it streams. The turn keeps
    // running on the server now (FR-016), so a reload seeds the question, calls
    // the resume route for the running turn, and renders the answer the replay
    // and tail carry — once, not twice.
    const running: AskTurn = {
      ...STORED_TURN,
      turn_id: "turn-7",
      status: "running" as const,
      answer: null,
      grounding: null,
      citations: [],
      passages: PASSAGES,
      retrieval_query: "RuBisCO",
    };
    vi.spyOn(askChatService, "listThreads").mockResolvedValue([THREAD] as never);
    vi.spyOn(askChatService, "getThread").mockResolvedValue({
      thread: THREAD,
      turns: [running],
    } as never);
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        urls.push(String(input));
        // The resume replays the frames the turn has published and its terminal
        // frame, which is what a reader that reattached mid-answer receives.
        return streamResponse([
          frame({ type: "start", messageId: "turn-7" }),
          frame({ type: "start-step" }),
          frame({ type: "text-start", id: "s" }),
          frame({ type: "text-delta", id: "s", delta: "RuBisCO fixes carbon " }),
          frame({ type: "text-delta", id: "s", delta: "and is slow [1]." }),
          frame({ type: "text-end", id: "s" }),
          frame({ type: "finish-step" }),
          frame({
            type: "data-deepfix-grounding",
            data: {
              grounding: "grounded",
              citations: [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
              max_score: 0.85,
              cutoff: 0.3,
            },
          }),
          frame({ type: "data-deepfix-turn", data: { turn_id: "turn-7", status: "completed" } }),
          frame({ type: "finish", finishReason: "stop" }),
          "data: [DONE]\n\n",
        ]);
      }),
    );
    renderPage(<AskPage indexReady />);

    // The answer arrives, once, from the resume the reload issued.
    await waitFor(() => {
      expect(screen.getByText(/RuBisCO fixes carbon and is slow/)).toBeTruthy();
    });
    expect(urls.filter((url) => url.includes("/turns/turn-7/stream"))).toHaveLength(1);
    expect(screen.getAllByText(/RuBisCO fixes carbon/)).toHaveLength(1);
    // The turn searched, so it is Grounded — not a reuse of some earlier answer.
    expect(screen.getByText("Grounded")).toBeTruthy();
    expect(screen.queryByText("Reused earlier evidence")).toBeNull();
    // Its evidence is on screen and its marker is a control, not a glyph.
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/[\uE000\uE001]/);
  });

  it("does not give a running turn the previous answer's badge", async () => {
    // The reported thread: an answered question, then a new one still streaming,
    // then a reload. The new turn has no verdict yet, so there is no badge to
    // show — borrowing the previous answer's is how the page claimed a verdict
    // about an answer that did not exist, and picked the wrong one ("Reused
    // earlier evidence" over "Grounded") for the turn that was actually running.
    const answered: AskTurn = {
      ...STORED_TURN,
      turn_id: "turn-1",
      grounding: "grounded",
      citations: [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
      passages: PASSAGES,
      retrieval_query: "Trunkenheitsfahrt",
      answer: "Die Trunkenheitsfahrt ist strafbar [1].",
    };
    const running: AskTurn = {
      ...STORED_TURN,
      turn_id: "turn-2",
      seq: 2,
      status: "running" as const,
      answer: null,
      grounding: null,
      citations: [],
      passages: PASSAGES,
      retrieval_query: "RuBisCO",
      interaction_id: null,
    };
    vi.spyOn(askChatService, "listThreads").mockResolvedValue([THREAD] as never);
    vi.spyOn(askChatService, "getThread").mockResolvedValue({
      thread: THREAD,
      turns: [answered, running],
    } as never);
    vi.stubGlobal(
      "fetch",
      // The resume hangs: this case is about what the reload draws before any
      // further frame arrives, which is exactly the window the bug appeared in.
      vi.fn(async () => new Response(new ReadableStream<Uint8Array>({ start() {} }))),
    );
    renderPage(<AskPage indexReady />);
    await screen.findByText(/Die Trunkenheitsfahrt ist strafbar/);
    // The badge describes the newest turn, and that turn has no verdict yet — so
    // there is no badge. Inheriting the previous answer's verdict is what made
    // this thread read "Reused earlier evidence" over a turn that had searched.
    expect(screen.queryByText("Reused earlier evidence")).toBeNull();
    expect(screen.queryByText("Partly grounded")).toBeNull();
    expect(screen.queryByText("Grounded")).toBeNull();
  });

  it("names a thread from its first question once the turn is stored", async () => {
    // A brand-new thread has no title until its first turn is stored, so the very
    // first read returns an empty one. The server derives the title from the first
    // question while the turn runs, and the list must pick it up rather than leave
    // the row saying "Untitled conversation" forever.
    vi.spyOn(askChatService, "listThreads").mockResolvedValue([{ ...THREAD, title: "" }] as never);
    let reads = 0;
    vi.spyOn(askChatService, "getThread").mockImplementation(async () => {
      reads += 1;
      // Before the turn there is nothing to name it from; after it, there is.
      return (
        reads === 1
          ? { thread: { ...THREAD, title: "" }, turns: [] }
          : {
              thread: { ...THREAD, title: "Ist eine Trunkenheitsfahrt strafbar?" },
              turns: [STORED_TURN],
            }
      ) as never;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => streamResponse(answerFrames("turn-1", ["Eine ", "Antwort [1]."]))),
    );
    renderPage(<AskPage indexReady />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy());
    fireEvent.change(screen.getByRole("textbox", { name: "Ask" }), {
      target: { value: "Ist eine Trunkenheitsfahrt strafbar?" },
    });
    fireEvent.submit(screen.getByRole("textbox", { name: "Ask" }).closest("form")!);
    await waitFor(() => {
      const nav = screen.getByRole("navigation", { name: "Conversations" });
      expect(nav.textContent).toContain("Ist eine Trunkenheitsfahrt strafbar?");
    });
  });
});

describe("a stopped turn's badge", () => {
  it("says the answer was stopped, rather than calling it partly grounded", async () => {
    // The reported defect: every stopped turn read "Partly grounded". A cancel
    // leaves the evidence intact but the prose unfinished, and `grounded` with no
    // citations is exactly the pair that means "the answer ignored its evidence" —
    // so the member was told their answer was deficient when they had asked it to
    // stop. The turn's own status has to decide before any judgement about
    // citations.
    const stoppedPartial = {
      ...STORED_TURN,
      turn_id: "turn-9",
      status: "cancelled" as const,
      // Text read so far, with no marker: the shape that used to read as defective.
      answer: "Die Trunkenheitsfahrt ist",
      citations: [],
      grounding: "grounded" as const,
      passages: PASSAGES,
      retrieval_query: "Trunkenheitsfahrt",
    };
    stubApi({ threads: [THREAD], detail: { thread: THREAD, turns: [stoppedPartial] } }, () =>
      streamResponse(answerFrames()),
    );
    renderPage(<AskPage indexReady />);
    await waitFor(() => {
      expect(screen.getByText("Stopped, partial answer")).toBeTruthy();
    });
    expect(screen.queryByText("Partly grounded")).toBeNull();
    expect(screen.queryByText("Grounded")).toBeNull();
  });

  it("says a stop that produced no words produced no answer", async () => {
    // Stopped before the first token. There is nothing to judge, so there is no
    // verdict — and certainly not "Partly grounded", which describes an answer.
    const stoppedEmpty = {
      ...STORED_TURN,
      turn_id: "turn-8",
      status: "cancelled" as const,
      answer: "",
      citations: [],
      grounding: "grounded" as const,
      passages: PASSAGES,
      retrieval_query: "Trunkenheitsfahrt",
    };
    stubApi({ threads: [THREAD], detail: { thread: THREAD, turns: [stoppedEmpty] } }, () =>
      streamResponse(answerFrames()),
    );
    renderPage(<AskPage indexReady />);
    await waitFor(() => {
      expect(screen.getByText("Stopped before answering")).toBeTruthy();
    });
    expect(screen.queryByText("Partly grounded")).toBeNull();
  });

  it("draws the stopped badge from the stream, without a reload", async () => {
    // The cancelled stream now closes its text group and sends the verdict, so a
    // member who pressed Stop sees what the turn reached immediately. Before this
    // the frames stopped at `text-delta` and the view only filled in on reload.
    const stopped = {
      ...STORED_TURN,
      turn_id: "turn-7",
      status: "cancelled" as const,
      answer: "Ein halber Satz.",
      citations: [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
      grounding: "grounded" as const,
      passages: PASSAGES,
      retrieval_query: "Trunkenheitsfahrt",
    };
    stubApi({ threads: [THREAD], detail: { thread: THREAD, turns: [] } }, () =>
      streamResponse([
        frame({ type: "start", messageId: "turn-7" }),
        frame({ type: "start-step" }),
        frame({ type: "text-start", id: "s" }),
        frame({ type: "text-delta", id: "s", delta: "Ein halber Satz." }),
        // The stop's own closing frames: this is what the fix adds.
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
        frame({ type: "data-deepfix-turn", data: { turn_id: "turn-7", status: "cancelled" } }),
        frame({ type: "finish", finishReason: "error" }),
        "data: [DONE]\n\n",
      ]),
    );
    let reads = 0;
    vi.spyOn(askChatService, "getThread").mockImplementation(async () => {
      reads += 1;
      return (
        reads === 1 ? { thread: THREAD, turns: [] } : { thread: THREAD, turns: [stopped] }
      ) as never;
    });
    renderPage(<AskPage indexReady />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy());
    fireEvent.change(screen.getByRole("textbox", { name: "Ask" }), {
      target: { value: "Lange Frage" },
    });
    fireEvent.submit(screen.getByRole("textbox", { name: "Ask" }).closest("form")!);
    // The badge describes the stopped turn, and it is not a judgement of its prose.
    await waitFor(() => {
      expect(screen.getByText("Stopped, partial answer")).toBeTruthy();
    });
    expect(screen.queryByText("Partly grounded")).toBeNull();
  });
});

describe("a reasoning model", () => {
  it("says it is thinking while the model reasons, then shows the answer", async () => {
    // A reasoning model can be silent for a long time before its first word. The
    // turn reports a `reasoning` status so the page says "Thinking…" instead of
    // looking hung, and the note disappears once prose arrives (D-12).
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    stubApi({ threads: [THREAD], detail: { thread: THREAD, turns: [] } }, () =>
      streamGatedAt(
        [
          frame({ type: "start", messageId: "turn-6" }),
          frame({ type: "start-step" }),
          frame({ type: "data-deepfix-status", data: { status: "reasoning" } }),
          frame({ type: "text-start", id: "s" }),
          frame({ type: "text-delta", id: "s", delta: "Die Antwort [1]." }),
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
          frame({ type: "data-deepfix-turn", data: { turn_id: "turn-6", status: "completed" } }),
          frame({ type: "finish", finishReason: "stop" }),
          "data: [DONE]\n\n",
        ],
        // Everything up to and including the status arrives before the gate; the
        // prose and the terminal frames wait, which is the silent gap a reasoning
        // model produces.
        3,
        gate,
      ),
    );
    renderPage(<AskPage indexReady />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy());
    fireEvent.change(screen.getByRole("textbox", { name: "Ask" }), {
      target: { value: "Lange Frage" },
    });
    fireEvent.submit(screen.getByRole("textbox", { name: "Ask" }).closest("form")!);
    await screen.findByTestId("ask-chat-thinking");
    expect(screen.getByRole("status").textContent).toContain("Thinking");
    release();
    // The note is gone once the turn ends, and the answer is there.
    await waitFor(() => expect(screen.queryByTestId("ask-chat-thinking")).toBeNull());
    expect(screen.getByText(/Die Antwort/)).toBeTruthy();
  });
});

describe("the badge as a stop settles", () => {
  it("switches to the stopped state at once, before the store has settled the row", async () => {
    // The reported defect: right after Stop the badge still read "Grounded", and
    // only a reload showed the stopped state. Cancel is a request, so the re-read
    // that follows the stream can land while the server still calls the turn
    // running — and the badge then fell back to the grounding verdict. The member's
    // intent is known the moment they press Stop, so the badge reads it.
    const runningTurn = {
      ...STORED_TURN,
      turn_id: "turn-10",
      status: "running" as const,
      answer: "Ein halber Satz.",
      grounding: "grounded" as const,
      citations: [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
      passages: PASSAGES,
      retrieval_query: "Trunkenheitsfahrt",
    };
    stubApi({ threads: [THREAD], detail: { thread: THREAD, turns: [] } }, () =>
      openStream(["Ein ", "halber ", "Satz."], "turn-10"),
    );
    let reads = 0;
    vi.spyOn(askChatService, "getThread").mockImplementation(async () => {
      reads += 1;
      // After the stop the store still says `running`: the settle has not landed.
      return (
        reads === 1 ? { thread: THREAD, turns: [] } : { thread: THREAD, turns: [runningTurn] }
      ) as never;
    });
    vi.spyOn(askChatService, "cancelTurn").mockResolvedValue(undefined);
    renderPage(<AskPage indexReady />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy());
    fireEvent.change(screen.getByRole("textbox", { name: "Ask" }), {
      target: { value: "Lange Frage" },
    });
    fireEvent.submit(screen.getByRole("textbox", { name: "Ask" }).closest("form")!);
    await screen.findByText(/Ein/);
    fireEvent.click(await screen.findByRole("button", { name: "Stop generating" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy());
    await waitFor(() => {
      expect(screen.getByText("Stopped, partial answer")).toBeTruthy();
    });
    expect(screen.queryByText("Grounded")).toBeNull();
  });

  it("keeps the stopped state after a reload, even with no verdict stored", async () => {
    // The reported defect: a turn stopped before its first word showed the badge
    // live, but after a reload the header was blank. The badge was only drawn when
    // a verdict existed, and a cancelled turn can have none — so the stored status
    // is what has to draw it.
    for (const grounding of ["grounded", "not_searched", null] as const) {
      const stoppedEmpty: AskTurn = {
        ...STORED_TURN,
        turn_id: "turn-8",
        status: "cancelled",
        answer: "",
        citations: [],
        grounding: grounding as never,
        passages: PASSAGES,
        retrieval_query: "Trunkenheitsfahrt",
      };
      vi.spyOn(askChatService, "listThreads").mockResolvedValue([THREAD] as never);
      vi.spyOn(askChatService, "getThread").mockResolvedValue({
        thread: THREAD,
        turns: [stoppedEmpty],
      } as never);
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => new Response("data: [DONE]\n\n", { status: 200 })),
      );
      const { unmount } = renderPage(<AskPage indexReady />);
      await waitFor(() => {
        expect(screen.getByText("Stopped before answering")).toBeTruthy();
      });
      expect(screen.queryByText("Partly grounded")).toBeNull();
      unmount();
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
    }
  });
});

describe("the page inside the real app shell", () => {
  it("lets the shell's main scroll the stacked sources into view", async () => {
    // The reported defect: at 250% zoom the sources panel could not be scrolled to
    // at all, and the composer hint was clipped. The cause was the page's own
    // `flex-1 min-h-0 overflow-hidden` chain: it gave the columns no height of their
    // own below `lg`, so the panel was clipped and `<main>` never had anything to
    // scroll. A harness that rendered the page alone could not see this — the shell
    // is the scroll container — so this renders the real `AppShell` around the real
    // page through a real router.
    const root = createRootRoute();
    const chat = createRoute({
      getParentRoute: () => root,
      path: "/chat",
      // The shell is inside the route, exactly as `__root` renders it: it calls
      // `useActivePath`, which needs a router above it.
      component: () => (
        <AppShell user={{ id: "u1", email: "ada@example.com", role: "member" } as never}>
          <AskPage indexReady />
        </AppShell>
      ),
    });
    const search = createRoute({
      getParentRoute: () => root,
      path: "/search",
      component: () => null,
    });
    const router = createRouter({
      routeTree: root.addChildren([chat, search]),
      history: createMemoryHistory({ initialEntries: ["/chat"] }),
    });
    stubApi(
      {
        threads: [THREAD],
        // The panel only renders when the stored turn carries the passages it
        // found, so the fixture has to include them.
        detail: { thread: THREAD, turns: [{ ...STORED_TURN, passages: PASSAGES }] },
      },
      () => streamResponse(answerFrames()),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    await screen.findByRole("navigation", { name: "Conversations" });
    await waitFor(() => {
      expect(screen.getByRole("complementary", { name: "Sources" })).toBeTruthy();
    });

    // The shell's `<main>` is the scroll container, and the page must not clip its
    // own content below `lg` — that is what made the sources unreachable.
    const main = document.querySelector("main")!;
    expect(main.className).toContain("overflow-auto");
    const page = main.firstElementChild as HTMLElement;
    expect(page.className.split(/\s+/)).not.toContain("overflow-hidden");
    expect(page.className).not.toMatch(/(^|\s)min-h-\[calc/);

    // The row that holds the chat and the sources must not be a `min-h-0 flex-1`
    // box below `lg`: with one, the columns get no height and the panel is clipped.
    const panel = screen.getByRole("complementary", { name: "Sources" });
    const column = panel.parentElement!;
    const row = column.parentElement!;
    for (const el of [row, column.parentElement!.previousElementSibling]) {
      expect(el!.className.split(/\s+/)).not.toContain("overflow-hidden");
    }
    // The sources column caps itself rather than claiming a share of a squashed row.
    expect(column.className).toContain("max-h-72");
    expect(column.className.split(/\s+/)).toContain("shrink-0");
  });
});

describe("a stopped turn that wrote nothing, after an answered one", () => {
  it("badges the newest turn, not the previous answer's text", async () => {
    // The reported defect: in a thread whose previous turn was answered, a new turn
    // stopped before any text badge "Stopped before answering" live, but after a
    // reload it showed the previous turn's "Stopped, partial answer". The text was
    // read from the newest *assistant message*, and an empty stopped turn seeds no
    // message — so the previous answer's words were used to decide the state.
    const answered: AskTurn = {
      ...STORED_TURN,
      turn_id: "turn-1",
      answer: "Die Trunkenheitsfahrt ist strafbar [1].",
      citations: [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
      passages: PASSAGES,
      retrieval_query: "Trunkenheitsfahrt",
    };
    const stoppedEmpty: AskTurn = {
      ...STORED_TURN,
      turn_id: "turn-2",
      seq: 2,
      status: "cancelled",
      answer: "",
      citations: [],
      interaction_id: "i2",
      passages: PASSAGES,
      retrieval_query: "RuBisCO",
    };
    stubApi(
      { threads: [THREAD], detail: { thread: THREAD, turns: [answered, stoppedEmpty] } },
      () => streamResponse(answerFrames()),
    );
    renderPage(<AskPage indexReady />);
    await waitFor(() => {
      expect(screen.getByText("Stopped before answering")).toBeTruthy();
    });
    expect(screen.queryByText("Stopped, partial answer")).toBeNull();
  });
});

describe("the reasoning note when the member stops", () => {
  it("clears Thinking… the moment Stop is pressed", async () => {
    // The reported cosmetic defect: "Thinking…" lingered about two seconds after
    // Stop, because the note was only cleared when a terminal frame or the thread
    // re-read arrived. Nothing is being written any more, so it goes at once.
    stubApi({ threads: [THREAD], detail: { thread: THREAD, turns: [] } }, () =>
      openStreamWithStatus(["Ein ", "halber"], "turn-11"),
    );
    vi.spyOn(askChatService, "cancelTurn").mockResolvedValue(undefined);
    renderPage(<AskPage indexReady />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy());
    fireEvent.change(screen.getByRole("textbox", { name: "Ask" }), {
      target: { value: "Lange Frage" },
    });
    fireEvent.submit(screen.getByRole("textbox", { name: "Ask" }).closest("form")!);
    // The stream reports `reasoning` and then stalls with the text group still open.
    await screen.findByTestId("ask-chat-thinking");
    fireEvent.click(await screen.findByRole("button", { name: "Stop generating" }));
    // Not `waitFor`: the note must be gone on the very next render. Waiting accepts
    // the old behaviour, where the thread re-read cleared it about two seconds later.
    expect(screen.queryByTestId("ask-chat-thinking")).toBeNull();
  });
});
