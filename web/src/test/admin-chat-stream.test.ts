import { describe, expect, it, vi } from "vitest";

import { parseFrame, splitFrames, streamTurn } from "@/services/admin-chat-sse";

/**
 * The stream reader, driven against the AI SDK UI message stream shape the
 * backend produces. A `Response` built from a `ReadableStream` is the fixture,
 * so the reader sees the same bytes it would see over the wire, split at
 * arbitrary points.
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
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

async function run(chunks: string[], signal?: AbortSignal) {
  const fetchMock = vi.fn().mockResolvedValue(responseFrom(chunks));
  vi.stubGlobal("fetch", fetchMock);
  const text: string[] = [];
  const tools: unknown[] = [];
  const turns: unknown[] = [];
  const errors: string[] = [];
  let doneCount = 0;
  await streamTurn(
    {
      threadId: "t1",
      messages: [{ role: "user", content: "Hallo" }],
      signal,
    },
    {
      onText: (delta) => text.push(delta),
      onTool: (progress) => tools.push(progress),
      onTurn: (turn) => turns.push(turn),
      onError: (message) => errors.push(message),
      onDone: () => {
        doneCount += 1;
      },
    },
  );
  vi.unstubAllGlobals();
  return { text, tools, turns, errors, doneCount, fetchMock };
}

describe("the admin chat stream reader", () => {
  it("accumulates a normal multi-chunk reply and calls onDone once", async () => {
    const { text, doneCount, errors } = await run([
      frame({ type: "start", messageId: "turn-1" }),
      frame({ type: "text-start", id: "turn-1-text" }),
      frame({ type: "text-delta", id: "turn-1-text", delta: "Das " }),
      frame({ type: "text-delta", id: "turn-1-text", delta: "ist " }),
      frame({ type: "text-delta", id: "turn-1-text", delta: "eine Antwort." }),
      frame({ type: "text-end", id: "turn-1-text" }),
      "data: [DONE]\n\n",
    ]);
    expect(text.join("")).toBe("Das ist eine Antwort.");
    expect(doneCount).toBe(1);
    expect(errors).toEqual([]);
  });

  it("surfaces the turn frame and its action card", async () => {
    const card = {
      action_id: "a1",
      turn_id: "turn-1",
      kind: "start_optimization_run",
      payload: { reason: "ready" },
      status: "proposed",
      created_at: "t",
      expires_at: "t+600",
    };
    const { turns } = await run([
      frame({ type: "text-delta", id: "x", delta: "ok" }),
      frame({
        type: "data-deepfix-turn",
        data: { turn_id: "turn-1", status: "completed", actions: [card] },
      }),
      frame({ type: "finish", finishReason: "stop" }),
      "data: [DONE]\n\n",
    ]);
    expect(turns).toHaveLength(1);
    expect(turns[0]).toMatchObject({
      turn_id: "turn-1",
      status: "completed",
      actions: [{ action_id: "a1", kind: "start_optimization_run" }],
    });
  });

  it("surfaces a tool-progress part", async () => {
    const { tools } = await run([
      frame({ type: "data-deepfix-tool", data: { label: "Searching the web…" } }),
      "data: [DONE]\n\n",
    ]);
    expect(tools).toEqual([{ label: "Searching the web…" }]);
  });

  it("surfaces an error part once", async () => {
    const { errors } = await run([
      frame({ type: "text-delta", id: "x", delta: "partial" }),
      frame({ type: "error", errorText: "The assistant could not answer. Try again." }),
      "data: [DONE]\n\n",
    ]);
    expect(errors).toEqual(["The assistant could not answer. Try again."]);
  });

  it("tolerates a frame split across reads", async () => {
    const whole = frame({ type: "text-delta", id: "x", delta: "split in two" });
    const { text } = await run([whole.slice(0, 20), whole.slice(20), "data: [DONE]\n\n"]);
    expect(text.join("")).toBe("split in two");
  });

  it("ignores keepalive comments", async () => {
    const { text, errors } = await run([
      ": keepalive\n\n",
      frame({ type: "text-delta", id: "x", delta: "ok" }),
      ": keepalive\n\n",
      "data: [DONE]\n\n",
    ]);
    expect(text.join("")).toBe("ok");
    expect(errors).toEqual([]);
  });

  it("reports a non-OK response body and calls onDone once", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: "chat_disabled",
              message: "Hermes chat is turned off on this server.",
            },
          }),
          { status: 503, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const errors: string[] = [];
    let done = 0;
    await streamTurn(
      { threadId: "t", messages: [{ role: "user", content: "hi" }] },
      { onError: (message) => errors.push(message), onDone: () => (done += 1) },
    );
    vi.unstubAllGlobals();
    expect(errors).toEqual(["Hermes chat is turned off on this server."]);
    expect(done).toBe(1);
  });

  it("treats an abort as one ended stream, not an error", async () => {
    const controller = new AbortController();
    const encoder = new TextEncoder();
    let sent = false;
    const stream = new ReadableStream<Uint8Array>({
      pull(streamController) {
        if (!sent) {
          sent = true;
          streamController.enqueue(
            encoder.encode(frame({ type: "text-delta", id: "x", delta: "partial" })),
          );
          return;
        }
        return new Promise<void>((_resolve, reject) => {
          controller.signal.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        });
      },
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
        ),
    );
    const text: string[] = [];
    const errors: string[] = [];
    let done = 0;
    const reading = streamTurn(
      { threadId: "t", messages: [{ role: "user", content: "hi" }], signal: controller.signal },
      {
        onText: (delta) => text.push(delta),
        onError: (message) => errors.push(message),
        onDone: () => (done += 1),
      },
    );
    await vi.waitFor(() => expect(text).toEqual(["partial"]));
    controller.abort();
    await reading;
    vi.unstubAllGlobals();
    expect(text.join("")).toBe("partial");
    expect(errors).toEqual([]);
    expect(done).toBe(1);
  });
});

describe("the frame helpers", () => {
  it("keeps an incomplete tail", () => {
    const { frames, rest } = splitFrames('data: {"a":1}\n\ndata: {"b"');
    expect(frames).toEqual(['data: {"a":1}']);
    expect(rest).toBe('data: {"b"');
  });

  it("drops a comment and an unknown part", () => {
    expect(parseFrame(": keepalive")).toBeNull();
    expect(parseFrame("data: [DONE]")).toBeNull();
    expect(parseFrame('data: {"type":"something-else"}')).toBeNull();
    expect(parseFrame("data: not-json")).toBeNull();
  });
});
