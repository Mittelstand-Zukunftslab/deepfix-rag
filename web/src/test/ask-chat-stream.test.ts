import { Chat } from "@ai-sdk/react";
import type { UIMessage } from "ai";
import { describe, expect, it, vi } from "vitest";

import {
  createAskChat,
  seedMessages,
  wireMessages,
  type AskChatHooks,
  type AskThreadChat,
} from "@/features/chat/ask-chat-transport";
import type { AskTurn } from "@/schemas/ask-chat";
import { parseFrame, splitFrames, streamTurn } from "@/services/ask-chat-sse";

/**
 * The Ask chat's data layer, driven through the real transport and the real AI
 * SDK `Chat`.
 *
 * The frames are the ones `tests/test_ask_chat_stream.py` asserts the backend
 * emits — a tool call, its output, then text — so the fixture is the real wire
 * shape rather than an invented one. A `Response` built from a `ReadableStream`
 * is stubbed in at the fetch boundary, which is the transport's own edge; the
 * parser, the `Chat`, and the hooks all run for real.
 */

function frame(part: unknown): string {
  return `data: ${JSON.stringify(part)}\n\n`;
}

function responseFrom(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

const SEARCH_OUTPUT = {
  query: "Trunkenheitsfahrt",
  grounding: "grounded",
  passages: [
    {
      rank: 1,
      doc_id: "doc_de_001",
      filename: "doc_de_001.txt",
      text: "Gemäß § 315c StGB …",
      score: 0.83,
      truncated: false,
    },
    {
      rank: 2,
      doc_id: "doc_de_002",
      filename: "doc_de_002.txt",
      text: "Die grobe Verletzung …",
      score: 0.41,
      truncated: true,
    },
  ],
  max_score: 0.83,
};

const GROUNDING = {
  grounding: "grounded",
  citations: [{ marker: 1, rank: 1, doc_id: "doc_de_001" }],
  max_score: 0.83,
  cutoff: 0.3,
};

/** One turn's frames, exactly as the backend emits them. */
function turnFrames(turnId = "turn-1", text = "Die Trunkenheitsfahrt ist strafbar [1]."): string[] {
  return [
    frame({ type: "start", messageId: turnId }),
    frame({ type: "start-step" }),
    frame({ type: "text-start", id: "step-0-text" }),
    frame({ type: "text-end", id: "step-0-text" }),
    frame({ type: "finish-step" }),
    frame({ type: "start-step" }),
    frame({ type: "tool-input-start", toolCallId: "call-1", toolName: "search_documents" }),
    frame({
      type: "tool-input-available",
      toolCallId: "call-1",
      toolName: "search_documents",
      input: { query: "Trunkenheitsfahrt" },
    }),
    frame({ type: "tool-output-available", toolCallId: "call-1", output: SEARCH_OUTPUT }),
    frame({ type: "text-start", id: "step-1-text" }),
    ...text.split(/(?<= )/).map((delta) => frame({ type: "text-delta", id: "step-1-text", delta })),
    frame({ type: "text-end", id: "step-1-text" }),
    frame({ type: "finish-step" }),
    frame({ type: "data-deepfix-grounding", data: GROUNDING }),
    frame({ type: "data-deepfix-turn", data: { turn_id: turnId, status: "completed" } }),
    frame({ type: "finish", finishReason: "stop" }),
    "data: [DONE]\n\n",
  ];
}

function hooks(): { current: AskChatHooks; seen: { grounding: unknown[]; turns: unknown[] } } {
  const seen = { grounding: [], turns: [] } as { grounding: unknown[]; turns: unknown[] };
  return {
    seen,
    current: {
      onGrounding: (value) => seen.grounding.push(value),
      onTurn: (value) => seen.turns.push(value),
      onError: () => undefined,
      onFinished: () => undefined,
    },
  };
}

async function settle(chat: Chat<UIMessage>): Promise<void> {
  // The transport resolves through the real parser; yield until the stream ends.
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (chat.status === "ready" || chat.status === "error") return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

describe("the ask chat transport", () => {
  it("turns the real frames into one assistant message with text, tools and grounding", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFrom(turnFrames())));
    const session = createAskChat("thread-1", [], hooks());
    await session.chat.sendMessage({ text: "Ist eine Trunkenheitsfahrt strafbar?" });
    await settle(session.chat);
    vi.unstubAllGlobals();

    const messages = session.chat.messages;
    const assistants = messages.filter((message) => message.role === "assistant");
    expect(assistants).toHaveLength(1);
    const parts = assistants[0]?.parts ?? [];
    const text = parts.map((part) => (part.type === "text" ? part.text : "")).join("");
    expect(text).toContain("strafbar");
    const toolParts = parts.filter((part) => part.type.startsWith("tool-"));
    // One part per tool call: the SDK folds the input and the output of the same
    // `toolCallId` into one part, which is why a single call yields one part.
    expect(toolParts).toHaveLength(1);
    for (const part of toolParts) {
      expect(part.type).toBe("tool-search_documents");
    }
    // The tool's output is the D-2 object, so the sources panel can read it.
    const withOutput = toolParts.filter(
      (part) => "state" in part && part.state === "output-available",
    );
    expect(withOutput).toHaveLength(1);
    const first = withOutput[0] as unknown as { output: typeof SEARCH_OUTPUT };
    expect(first.output.passages).toHaveLength(2);
    expect(first.output.max_score).toBeCloseTo(0.83);

    // The grounding and the terminal turn arrive as data parts the transport reads.
    const dataParts = parts.filter((part) => part.type === "data-deepfix-grounding");
    expect(dataParts).toHaveLength(1);
  });

  it("keeps two threads apart, so a reply in one never lands in the other", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFrom(turnFrames("turn-a"))));
    const first = createAskChat("thread-a", [], hooks());
    await first.chat.sendMessage({ text: "Frage A" });
    await settle(first.chat);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(responseFrom(turnFrames("turn-b", "Andere Antwort [1]."))),
    );
    const second = createAskChat("thread-b", [], hooks());
    await second.chat.sendMessage({ text: "Frage B" });
    await settle(second.chat);
    vi.unstubAllGlobals();

    const firstText = first.chat.messages
      .flatMap((message) => message.parts)
      .map((part) => (part.type === "text" ? part.text : ""))
      .join("");
    const secondText = second.chat.messages
      .flatMap((message) => message.parts)
      .map((part) => (part.type === "text" ? part.text : ""))
      .join("");
    expect(firstText).not.toContain("Andere Antwort");
    expect(secondText).toContain("Andere Antwort");
    expect(secondText).not.toContain("Trunkenheitsfahrt ist strafbar");
  });

  it("seeds a stored thread so a reload draws its answer once", async () => {
    const stored: AskTurn[] = [
      {
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
      },
    ];
    const seed = seedMessages(stored);
    expect(seed.map((message) => message.role)).toEqual(["user", "assistant"]);
    // The assistant message id is the turn id, so a resumed stream replaces it.
    expect(seed[1]?.id).toBe("turn-1");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFrom(turnFrames("turn-1"))));
    const session = createAskChat("thread-1", seed, hooks());
    await settle(session.chat);
    vi.unstubAllGlobals();

    const answers = session.chat.messages.filter(
      (message) =>
        message.role === "assistant" &&
        message.parts.some((part) => part.type === "text" && part.text.includes("strafbar")),
    );
    expect(answers).toHaveLength(1);
    expect(session.chat.messages.filter((message) => message.role === "user")).toHaveLength(1);
  });

  it("sends the thread id and a regenerate flag and never a model", async () => {
    const fetchMock = vi.fn().mockResolvedValue(responseFrom(turnFrames()));
    vi.stubGlobal("fetch", fetchMock);
    const session: AskThreadChat = createAskChat("thread-7", [], hooks());
    await session.chat.sendMessage({ text: "Frage" });
    await settle(session.chat);
    vi.unstubAllGlobals();

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body["thread_id"]).toBe("thread-7");
    expect(body["regenerate"]).toBe(false);
    expect(body).not.toHaveProperty("model");
    expect(JSON.stringify(body)).not.toMatch(/qwen|openrouter|gpt/i);
  });
});

describe("the ask chat stream reader", () => {
  it("parses the frames and reports one error sentence from the API", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "You have used 20 of 20 questions." }), {
          status: 429,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const errors: string[] = [];
    await streamTurn(
      { threadId: "t1", messages: [{ role: "user", content: "Hallo" }] },
      { onError: (message) => errors.push(message) },
    );
    vi.unstubAllGlobals();
    expect(errors).toEqual(["You have used 20 of 20 questions."]);
  });

  it("splits a buffer at frame boundaries and ignores comments and [DONE]", () => {
    const { frames, rest } = splitFrames(': keepalive\n\ndata: [DONE]\n\ndata: {"type":"start"}');
    expect(frames).toHaveLength(2);
    expect(parseFrame(frames[0]!)).toBeNull();
    expect(parseFrame(frames[1]!)).toBeNull();
    expect(rest).toBe('data: {"type":"start"}');
  });

  it("validates a frame against its zod contract rather than trusting it", () => {
    expect(
      parseFrame(frame({ type: "data-deepfix-turn", data: { turn_id: "t", status: "completed" } })),
    ).toMatchObject({ type: "data-deepfix-turn" });
    // A malformed grounding frame is dropped, not rendered as undefined badges.
    expect(
      parseFrame(frame({ type: "data-deepfix-grounding", data: { grounding: "invented" } })),
    ).toBeNull();
  });

  it("wireMessages keeps only what the API validates a turn against", () => {
    const messages = seedMessages([
      {
        turn_id: "turn-1",
        seq: 1,
        status: "completed",
        citations: [],
        started_at: "2026-11-15T00:00:00+00:00",
        question: "Frage",
        answer: "Antwort",
      },
    ]);
    expect(wireMessages(messages)).toEqual([
      { role: "user", content: "Frage" },
      { role: "assistant", content: "Antwort" },
    ]);
  });
});
