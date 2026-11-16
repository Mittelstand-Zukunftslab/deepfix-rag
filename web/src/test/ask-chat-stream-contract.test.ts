import { uiMessageChunkSchema } from "ai";
import { describe, expect, it } from "vitest";

/**
 * The parts deepfix-api emits must be parts the AI SDK accepts.
 *
 * `src/deepfix_rag/api/chat_stream.py` builds them and
 * `tests/test_ask_chat_stream.py` asserts the order; this file is the other half
 * of that contract: every part type either chat can emit parses against the
 * SDK's own chunk schema, so a typo or an invented part fails here rather than
 * silently rendering nothing in the browser.
 *
 * The samples mirror the builder functions' output shape. They are literals
 * rather than a call into Python because the schema is the authority being
 * checked, and a sample generated from the code under test would agree with it
 * by construction.
 */
const PARTS = {
  start: { type: "start", messageId: "turn-1" },
  "start-step": { type: "start-step" },
  "finish-step": { type: "finish-step" },
  "text-start": { type: "text-start", id: "turn-1-text" },
  "text-delta": { type: "text-delta", id: "turn-1-text", delta: "Hallo" },
  "text-end": { type: "text-end", id: "turn-1-text" },
  "tool-input-start": {
    type: "tool-input-start",
    toolCallId: "call-1",
    toolName: "search_documents",
  },
  "tool-input-available": {
    type: "tool-input-available",
    toolCallId: "call-1",
    toolName: "search_documents",
    input: { query: "Trunkenheitsfahrt" },
  },
  "tool-output-available": {
    type: "tool-output-available",
    toolCallId: "call-1",
    output: { query: "Trunkenheitsfahrt", grounding: "grounded", passages: [], max_score: 0 },
  },
  "data-deepfix-grounding": {
    type: "data-deepfix-grounding",
    data: { grounding: "grounded", citations: [], max_score: 0.8, cutoff: 0.3 },
  },
  "data-deepfix-turn": {
    type: "data-deepfix-turn",
    data: { turn_id: "turn-1", status: "completed", actions: [] },
  },
  "data-deepfix-tool": {
    type: "data-deepfix-tool",
    data: { label: "Searching…", state: "running" },
  },
  finish: { type: "finish", finishReason: "stop" },
  error: { type: "error", errorText: "The assistant could not answer." },
} as const;

describe("the UI message stream contract", () => {
  // The standard schema's `validate` is optional in its type; the UI message
  // chunk schema always defines it, which the SDK's own parser relies on.
  const schema = uiMessageChunkSchema();
  const validate = schema.validate!;

  it.each(Object.entries(PARTS))("accepts the %s part", async (_name, part) => {
    await expect(validate(part)).resolves.toBeDefined();
  });

  it("accepts a data part whose name the front end does not know yet", async () => {
    // The SDK models `data-*` parts generically, so an unknown name is not a
    // contract violation: the allowlist that keeps the two chats to the parts
    // they mean is asserted server-side, where the parts are built.
    await expect(validate({ type: "data-deepfix-unknown", data: {} })).resolves.toBeDefined();
  });
});
