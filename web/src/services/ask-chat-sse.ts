import { z } from "zod";

import {
  askGroundingFrame,
  askTurnFrame,
  type AskGroundingFrame,
  type AskTurnFrame,
} from "@/schemas/ask-chat";

/**
 * The streaming half of the Ask chat: one fetch to a same-origin route.
 *
 * This is the second and last module in `web/src` that reads an event stream,
 * and it is in `api-boundary.test.ts`'s exemption set for exactly that reason.
 * The wire format is the Vercel AI SDK's UI message stream, which the backend
 * produces; this reader exists for the frames the generated client cannot
 * consume — the grounding verdict and the terminal turn — so the page can render
 * a badge and offer regenerate without reaching into the runtime's internals.
 *
 * Frames arrive as `data: {json}` terminated by a blank line, `data: [DONE]`
 * ends the stream, and a line beginning with `:` is a comment. That is
 * documented behaviour of the protocol, not a guess.
 */

export const STREAM_PATH = "/api/ask/chat/completions";
export const RESUME_PATH = "/api/ask/chat/turns";

export interface StreamHandlers {
  /** Every text delta, in order. */
  onText?: (delta: string) => void;
  /** The `data-deepfix-grounding` part. */
  onGrounding?: (frame: AskGroundingFrame) => void;
  /** The terminal `data-deepfix-turn` part. */
  onTurn?: (frame: AskTurnFrame) => void;
  /** A redacted failure sentence from an `error` part. */
  onError?: (message: string) => void;
  /** Called once, when the stream ends for any reason. */
  onDone?: () => void;
}

const groundingPart = z.object({
  type: z.literal("data-deepfix-grounding"),
  data: askGroundingFrame,
});
const turnPart = z.object({
  type: z.literal("data-deepfix-turn"),
  data: askTurnFrame,
});
const textDeltaPart = z.object({
  type: z.literal("text-delta"),
  delta: z.string(),
});
const errorPart = z.object({
  type: z.literal("error"),
  errorText: z.string(),
});

const partSchema = z.union([groundingPart, turnPart, textDeltaPart, errorPart]);

/** One parsed frame, or `null` for a comment, `[DONE]`, or a part this reader ignores. */
export function parseFrame(frame: string): z.infer<typeof partSchema> | null {
  const data = frame
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n")
    .trim();
  if (!data || data === "[DONE]") return null;
  let payload: unknown;
  try {
    payload = JSON.parse(data);
  } catch {
    return null;
  }
  const parsed = partSchema.safeParse(payload);
  return parsed.success ? parsed.data : null;
}

/** Split a buffer into complete frames, returning the leftovers to carry over. */
export function splitFrames(buffer: string): { frames: string[]; rest: string } {
  const frames: string[] = [];
  let rest = buffer;
  let boundary = rest.indexOf("\n\n");
  while (boundary !== -1) {
    frames.push(rest.slice(0, boundary));
    rest = rest.slice(boundary + 2);
    boundary = rest.indexOf("\n\n");
  }
  return { frames, rest };
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function failureMessage(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail.length > 0) return detail;
  if (status === 429) return "Too many questions. Wait a moment and try again.";
  if (status === 503) return "The documents are being prepared. Try again in a minute.";
  return "The answer could not be read. Try again.";
}

async function readStream(response: Response, handlers: StreamHandlers): Promise<void> {
  if (!response.body) {
    handlers.onError?.("The answer could not be read. Try again.");
    handlers.onDone?.();
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { frames, rest } = splitFrames(buffer);
      buffer = rest;
      for (const frame of frames) {
        const part = parseFrame(frame);
        if (!part) continue;
        if (part.type === "text-delta") handlers.onText?.(part.delta);
        else if (part.type === "data-deepfix-grounding") handlers.onGrounding?.(part.data);
        else if (part.type === "data-deepfix-turn") handlers.onTurn?.(part.data);
        else handlers.onError?.(part.errorText);
      }
    }
  } catch (error) {
    // An abort is the member pressing Stop, not a failure to report.
    if (!isAbort(error)) {
      handlers.onError?.("The answer stopped unexpectedly. Try again.");
    }
  } finally {
    reader.releaseLock();
    handlers.onDone?.();
  }
}

async function send(
  path: string,
  body: unknown,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(path, {
    method: body === null ? "GET" : "POST",
    credentials: "include",
    headers: {
      Accept: "text/event-stream",
      ...(body === null ? {} : { "Content-Type": "application/json" }),
    },
    body: body === null ? undefined : JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    handlers.onError?.(failureMessage(response.status, payload));
    handlers.onDone?.();
    return;
  }
  await readStream(response, handlers);
}

/** One question. Resolves when the stream ends; the handlers see every part. */
export async function streamTurn(
  input: {
    threadId: string;
    messages: { role: "user" | "assistant"; content: string }[];
    regenerate?: boolean;
    signal?: AbortSignal;
  },
  handlers: StreamHandlers,
): Promise<void> {
  await send(
    STREAM_PATH,
    {
      thread_id: input.threadId,
      messages: input.messages,
      regenerate: input.regenerate ?? false,
    },
    handlers,
    input.signal,
  );
}

/** Resume an answer the tab left behind: the stored text, then the live tail. */
export async function resumeTurn(
  turnId: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  await send(`${RESUME_PATH}/${encodeURIComponent(turnId)}/stream`, null, handlers, signal);
}
