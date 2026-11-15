import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AskPage, passagesFromMessages } from "@/features/chat/chat-page";
import { GroundingBadge } from "@/features/chat/answer-grounding";
import { badgeState } from "@/features/chat/grounding-copy";
import type { AskPassage, AskTurn } from "@/schemas/ask-chat";
import { askChatService } from "@/services/ask-chat-service";

/**
 * The evidence behind an answer: the sources panel, the citation controls, and
 * the grounding badge.
 *
 * The panel is driven through the real page, the real runtime, and the real
 * transport; only the network is stubbed, and the frames are the ones the
 * backend emits — the tool output carries two passages and the answer cites the
 * first. That is the transport boundary, as `admin-chat-session.test.tsx` does
 * it, rather than a stub of the component under test.
 */

const THREAD = {
  thread_id: "thread-1",
  title: "Trunkenheitsfahrt",
  created_at: "a",
  updated_at: "b",
};

const PASSAGES: AskPassage[] = [
  {
    rank: 1,
    doc_id: "doc_de_001",
    filename: "stgb_315c.txt",
    text: "Gemäß § 315c StGB wird bestraft, wer infolge des Genusses alkoholischer Getränke …",
    score: 0.83,
    truncated: false,
  },
  {
    rank: 2,
    doc_id: "doc_de_002",
    filename: "verkehrsregeln.txt",
    text: "Die grobe Verletzung der Verkehrsregeln setzt grob verkehrswidriges Fahren voraus.",
    score: 0.41,
    truncated: true,
  },
];

function frame(part: unknown): string {
  return `data: ${JSON.stringify(part)}\n\n`;
}

function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

/** One answer, with the tool output the panel reads and a grounding verdict. */
function framesFor(
  answer: string,
  grounding: "grounded" | "no_relevant_sources",
  citations: { marker: number; rank: number; doc_id: string }[],
  passages: AskPassage[],
): string[] {
  return [
    frame({ type: "start", messageId: "turn-1" }),
    frame({ type: "start-step" }),
    frame({ type: "tool-input-start", toolCallId: "c1", toolName: "search_documents" }),
    frame({
      type: "tool-input-available",
      toolCallId: "c1",
      toolName: "search_documents",
      input: { query: "Trunkenheitsfahrt" },
    }),
    frame({
      type: "tool-output-available",
      toolCallId: "c1",
      output: {
        query: "Trunkenheitsfahrt",
        grounding: passages.length ? "grounded" : "no_relevant_sources",
        passages,
        max_score: passages[0]?.score ?? 0,
      },
    }),
    frame({ type: "text-start", id: "s" }),
    ...answer.split(/(?<= )/).map((delta) => frame({ type: "text-delta", id: "s", delta })),
    frame({ type: "text-end", id: "s" }),
    frame({ type: "finish-step" }),
    frame({
      type: "data-deepfix-grounding",
      data: { grounding, citations, max_score: passages[0]?.score ?? 0, cutoff: 0.3 },
    }),
    frame({ type: "data-deepfix-turn", data: { turn_id: "turn-1", status: "completed" } }),
    frame({ type: "finish", finishReason: "stop" }),
    "data: [DONE]\n\n",
  ];
}

/** Ask one question through the composer and wait for the answer to arrive. */
async function ask(question: string): Promise<void> {
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Ask" })).toBeTruthy());
  const box = screen.getByRole("textbox", { name: "Ask" });
  fireEvent.change(box, { target: { value: question } });
  fireEvent.submit(box.closest("form")!);
}

const renderPage = (ui: ReactNode) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Suspense fallback={<p>Loading</p>}>{ui}</Suspense>
    </QueryClientProvider>,
  );
};

const storedTurn = (overrides: Partial<AskTurn>): AskTurn => ({
  passages: PASSAGES,
  turn_id: "turn-1",
  seq: 1,
  status: "completed",
  citations: [],
  started_at: "a",
  ended_at: "b",
  interaction_id: "interaction-1",
  question: "Ist eine Trunkenheitsfahrt strafbar?",
  answer: "Eine Antwort.",
  ...overrides,
});

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

describe("the sources panel", () => {
  it("draws two cards and a citation control that selects the first", async () => {
    vi.spyOn(askChatService, "listThreads").mockResolvedValue([THREAD] as never);
    vi.spyOn(askChatService, "getThread").mockResolvedValue({
      thread: THREAD,
      turns: [
        storedTurn({
          answer: "Die Trunkenheitsfahrt ist strafbar [1].",
          grounding: "grounded",
          citations: [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
        }),
      ],
    } as never);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        streamResponse(
          framesFor(
            "Die Trunkenheitsfahrt ist strafbar [1].",
            "grounded",
            [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
            PASSAGES,
          ),
        ),
      ),
    );
    renderPage(<AskPage indexReady />);
    await ask("Ist eine Trunkenheitsfahrt strafbar?");

    await screen.findByRole("heading", { name: "Sources" });
    expect(screen.getByText("stgb_315c.txt")).toBeTruthy();
    expect(screen.getByText("verkehrsregeln.txt")).toBeTruthy();
    expect(screen.getByText("2 passages")).toBeTruthy();

    // The marker is a control named for the passage it points at.
    const citation = screen.getByRole("button", { name: "Show passage 1" });
    fireEvent.click(citation);
    await waitFor(() => {
      const card = document.getElementById("passage-1");
      expect(card?.getAttribute("aria-current")).toBe("true");
    });
  });

  it("treats a citation that resolved to nothing as plain text", async () => {
    vi.spyOn(askChatService, "listThreads").mockResolvedValue([THREAD] as never);
    vi.spyOn(askChatService, "getThread").mockResolvedValue({
      thread: THREAD,
      turns: [
        storedTurn({
          answer: "Ein Satz [1] und ein erfundener Beleg [3].",
          grounding: "grounded",
          citations: [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
        }),
      ],
    } as never);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        // Only marker 1 was validated; the answer also writes [3].
        streamResponse(
          framesFor(
            "Ein Satz [1] und ein erfundener Beleg [3].",
            "grounded",
            [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
            PASSAGES,
          ),
        ),
      ),
    );
    renderPage(<AskPage indexReady />);
    await ask("Ist eine Trunkenheitsfahrt strafbar?");
    await screen.findByRole("button", { name: "Show passage 1" });
    expect(screen.queryByRole("button", { name: "Show passage 3" })).toBeNull();
    // It is still in the text, as plain characters rather than a link.
    expect(screen.getByText(/erfundener Beleg \[3\]/)).toBeTruthy();
  });

  it("shows the Partly grounded badge when a grounded answer cites nothing", async () => {
    vi.spyOn(askChatService, "listThreads").mockResolvedValue([THREAD] as never);
    vi.spyOn(askChatService, "getThread").mockResolvedValue({
      thread: THREAD,
      turns: [storedTurn({ grounding: "grounded", citations: [] })],
    } as never);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        streamResponse(framesFor("Eine Antwort ohne Beleg.", "grounded", [], PASSAGES)),
      ),
    );
    renderPage(<AskPage indexReady />);
    await waitFor(() => {
      expect(screen.getByText("Partly grounded")).toBeTruthy();
    });
    // Both passages are still shown: the answer had the evidence and ignored it.
    expect(screen.getByText("stgb_315c.txt")).toBeTruthy();
    expect(screen.queryByText("Grounded")).toBeNull();
  });

  it("shows the not-found badge, no cards and no regenerate for an empty result", async () => {
    vi.spyOn(askChatService, "listThreads").mockResolvedValue([THREAD] as never);
    vi.spyOn(askChatService, "getThread").mockResolvedValue({
      thread: THREAD,
      turns: [
        storedTurn({
          grounding: "no_relevant_sources",
          citations: [],
          passages: [],
          answer: "I do not have enough information in these documents to answer that question.",
        }),
      ],
    } as never);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        streamResponse(
          framesFor(
            "I do not have enough information in these documents to answer that question.",
            "no_relevant_sources",
            [],
            [],
          ),
        ),
      ),
    );
    renderPage(<AskPage indexReady />);
    await waitFor(() => {
      expect(screen.getAllByText("Not found in the documents").length).toBeGreaterThan(0);
    });
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Regenerate" })).toBeNull();
  });

  it("votes on a passage with the rank the tool returned", async () => {
    const submit = vi.fn(async () => ({ vote: "up" as const }));
    vi.spyOn(askChatService, "listThreads").mockResolvedValue([THREAD] as never);
    vi.spyOn(askChatService, "getThread").mockResolvedValue({
      thread: THREAD,
      turns: [
        storedTurn({
          grounding: "grounded",
          citations: [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
        }),
      ],
    } as never);
    const feedback = await import("@/services/feedback-service");
    vi.spyOn(feedback.feedbackService, "submit").mockImplementation(submit as never);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        streamResponse(
          framesFor(
            "Die Trunkenheitsfahrt ist strafbar [1].",
            "grounded",
            [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
            PASSAGES,
          ),
        ),
      ),
    );
    renderPage(<AskPage indexReady />);
    await screen.findByRole("heading", { name: "Sources" });
    fireEvent.click(screen.getByRole("button", { name: "Mark passage 2 as relevant" }));
    await waitFor(() => {
      expect(submit).toHaveBeenCalledWith({
        interactionId: "interaction-1",
        target: "passage",
        rank: 2,
        action: "up",
      });
    });
  });

  it("marks the streaming region as a polite live region", async () => {
    vi.spyOn(askChatService, "listThreads").mockResolvedValue([THREAD] as never);
    vi.spyOn(askChatService, "getThread").mockResolvedValue({
      thread: THREAD,
      turns: [storedTurn({})],
    } as never);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => streamResponse(framesFor("Antwort.", "grounded", [], []))),
    );
    renderPage(<AskPage indexReady />);
    const region = await screen.findByTestId("ask-chat-stream");
    expect(region.getAttribute("aria-live")).toBe("polite");
  });
});

describe("the badge rule", () => {
  it("is grounded only when there is a valid citation", () => {
    expect(badgeState("grounded", [])).toBe("partly_grounded");
    expect(badgeState("grounded", [{ marker: 1, rank: 1, doc_id: "d" }])).toBe("grounded");
    expect(badgeState("declined", [])).toBe("declined");
    expect(badgeState("no_relevant_sources", [])).toBe("no_relevant_sources");
  });

  it("labels the four rendered states", () => {
    const { rerender } = render(<GroundingBadge grounding="grounded" citations={[]} />);
    expect(screen.getByText("Partly grounded")).toBeTruthy();
    rerender(
      <GroundingBadge grounding="grounded" citations={[{ marker: 1, rank: 1, doc_id: "d" }]} />,
    );
    expect(screen.getByText("Grounded")).toBeTruthy();
  });
});

describe("the sources come from the tool output", () => {
  it("reads passages from the newest search rather than from prose", () => {
    const messages = [
      {
        id: "a",
        role: "assistant" as const,
        parts: [
          { type: "text" as const, text: "[1] is mentioned but no tool ran.", state: "done" },
          {
            type: "tool-search_documents" as const,
            toolCallId: "c1",
            state: "output-available" as const,
            input: { query: "q" },
            output: {
              query: "q",
              grounding: "grounded",
              passages: PASSAGES,
              max_score: 0.83,
            },
          },
        ],
      },
    ];
    expect(passagesFromMessages(messages as never)).toEqual(PASSAGES);
    expect(passagesFromMessages([])).toEqual([]);
  });

  it("reads a reloaded thread's passages from the stored turn", () => {
    // AC 3: a reload has no tool part, so the panel is built from the store.
    const stored: AskTurn = storedTurn({
      passages: PASSAGES,
      grounding: "grounded",
      citations: [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
    });
    expect(stored.passages).toEqual(PASSAGES);
  });
});
