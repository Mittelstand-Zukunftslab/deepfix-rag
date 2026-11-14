import { z } from "zod";

import {
  chatToolProgress,
  chatTurnFrame,
  type ChatToolProgress,
  type ChatTurnFrame,
} from "@/schemas/admin-chat";

/**
 * The streaming half of the admin chat: one fetch to the same-origin route.
 *
 * This is the only module in `web/src` that reads an event stream, and it is
 * exempt from `api-boundary.test.ts` for exactly that reason. The wire format is
 * the Vercel AI SDK's UI message stream, which the backend produces; this reader
 * exists so the JSON-free parts (a tool-progress part, the terminal turn part,
 * an error part) can be surfaced to the UI without reaching into the AI SDK
 * runtime's internal message shape.
 *
 * The frame syntax is documented behaviour, not a guess: parts arrive as
 * `data: {json}` terminated by a blank line, `data: [DONE]` ends the stream, and
 * a line beginning with `:` is a comment the server may send while it waits.
 */

export const STREAM_PATH = "/api/admin/chat/completions";
export const RESUME_PATH = "/api/admin/chat/turns";

export interface StreamHandlers {
  /** Every text delta, in order. */
  onText?: (delta: string) => void;
  /** One `data-deepfix-tool` part. */
  onTool?: (progress: ChatToolProgress) => void;
  /** The terminal `data-deepfix-turn` part. */
  onTurn?: (frame: ChatTurnFrame) => void;
  /** A redacted failure sentence from an `error` part. */
  onError?: (message: string) => void;
  /** Called once, when the stream ends for any reason. */
  onDone?: () => void;
}

const toolPart = z.object({
  type: z.literal("data-deepfix-tool"),
  data: chatToolProgress,
});
const turnPart = z.object({
  type: z.literal("data-deepfix-turn"),
  data: chatTurnFrame,
});
const textDeltaPart = z.object({
  type: z.literal("text-delta"),
  delta: z.string(),
});
const errorPart = z.object({
  type: z.literal("error"),
  errorText: z.string(),
});

const partSchema = z.union([toolPart, turnPart, textDeltaPart, errorPart]);

/** One parsed frame, or `null` for a comment, `[DONE]`, or an unknown part. */
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

async function readStream(response: Response, handlers: StreamHandlers): Promise<void> {
  if (!response.body) {
    handlers.onError?.("The reply could not be read. Try again.");
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
        else if (part.type === "data-deepfix-tool") handlers.onTool?.(part.data);
        else if (part.type === "data-deepfix-turn") handlers.onTurn?.(part.data);
        else handlers.onError?.(part.errorText);
      }
    }
  } catch (error) {
    if (!isAbort(error)) {
      handlers.onError?.("The reply stopped unexpectedly. Try again.");
    }
  } finally {
    reader.releaseLock();
    handlers.onDone?.();
  }
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function failureMessage(status: number, body: unknown): { message: string } {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return { message: detail };
  if (detail && typeof detail === "object" && "message" in detail) {
    const typed = detail as { message?: unknown };
    return { message: typeof typed.message === "string" ? typed.message : "The request failed." };
  }
  return {
    message: status === 429 ? "Too many requests. Try again shortly." : "The request failed.",
  };
}

/** One turn. Resolves when the stream ends; the handlers see every part. */
export async function streamTurn(
  input: {
    threadId: string;
    messages: { role: "user" | "assistant"; content: string }[];
    retryOf?: string | null;
    signal?: AbortSignal;
  },
  handlers: StreamHandlers,
): Promise<void> {
  await send(
    STREAM_PATH,
    {
      thread_id: input.threadId,
      messages: input.messages,
      retry_of: input.retryOf ?? null,
    },
    handlers,
    input.signal,
  );
}

/** Resume a turn the tab left behind: the stored text, then the live tail. */
export async function resumeTurn(
  turnId: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  await send(`${RESUME_PATH}/${encodeURIComponent(turnId)}/stream`, null, handlers, signal);
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
    body: body === null ? undefined : bodyString(body),
    signal,
  });
  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    handlers.onError?.(failureMessage(response.status, payload).message);
    handlers.onDone?.();
    return;
  }
  await readStream(response, handlers);
}

function bodyString(body: unknown): string {
  return JSON.stringify(body);
}
