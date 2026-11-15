import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");
/**
 * The Ask chat's stream reader is the second exempt module from
 * `api-boundary.test.ts`, so this file is what keeps that exemption narrow: it
 * may talk to `/api/ask/chat/` and to nothing else, and it may hold no model id,
 * no key, and no upstream host. The admin chat's equivalent test is
 * `admin-chat-boundary.test.ts`.
 */
describe("ask chat stream boundary", () => {
  const source = readFileSync(join(SRC, "services", "ask-chat-sse.ts"), "utf-8");

  it("only targets same-origin ask chat paths", () => {
    const literals = [...source.matchAll(/(["'`])(\/[^"'`\s]*)\1/g)].map((match) => match[2]);
    expect(literals.length).toBeGreaterThan(0);
    for (const path of literals) {
      expect(path?.startsWith("/api/ask/chat")).toBe(true);
    }
  });

  it("has no model id, key, or upstream host in it", () => {
    for (const forbidden of ["8643", "ASK_CHAT_MODEL", "OPENROUTER", "LLM_API_KEY", "hermes:"]) {
      expect(source).not.toContain(forbidden);
    }
  });

  it("reads the AI SDK stream protocol rather than a bespoke one", () => {
    expect(source).toContain('"data:"');
    expect(source).toContain("[DONE]");
    expect(source).toContain("text-delta");
    expect(source).toContain("data-deepfix-turn");
    expect(source).toContain("data-deepfix-grounding");
  });

  it("the service layer names no absolute URL", () => {
    const service = readFileSync(join(SRC, "services", "ask-chat-service.ts"), "utf-8");
    expect(service).not.toMatch(/https?:\/\//);
    // Every streaming call goes through the reader, so no component holds it.
    expect(service).toContain("streamTurn");
    expect(service).toContain("resumeTurn");
  });

  it("the transport sends a thread id and a regenerate flag, never a model", () => {
    const transport = readFileSync(join(SRC, "features", "chat", "ask-chat-transport.ts"), "utf-8");
    expect(transport).toContain("thread_id: threadId");
    expect(transport).toContain('trigger === "regenerate-message"');
    expect(transport).not.toMatch(/model\s*:/);
  });
});
