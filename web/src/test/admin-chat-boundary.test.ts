import { readdirSync, readFileSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");

/**
 * The stream reader is the one exempt module from `api-boundary.test.ts`, so
 * this file is what keeps the exemption narrow: it may talk to `/api/admin/chat/`
 * and to nothing else, and it may not know a model id, a key, or a Hermes host.
 */
describe("admin chat stream boundary", () => {
  const source = readFileSync(join(SRC, "services", "admin-chat-sse.ts"), "utf-8");

  it("only targets same-origin admin chat paths", () => {
    const literals = [...source.matchAll(/(["'`])(\/[^"'`\s]*)\1/g)].map((match) => match[2]);
    expect(literals.length).toBeGreaterThan(0);
    for (const path of literals) {
      expect(path?.startsWith("/api/admin/chat")).toBe(true);
    }
  });

  it("has no model id, key, or Hermes host in it", () => {
    for (const forbidden of ["8643", "HERMES_CHAT", "OPENROUTER", "API_SERVER_KEY", "hermes:"]) {
      expect(source).not.toContain(forbidden);
    }
  });

  it("reads the AI SDK stream protocol rather than a bespoke one", () => {
    expect(source).toContain('"data:"');
    expect(source).toContain("[DONE]");
    expect(source).toContain("text-delta");
    expect(source).toContain("data-deepfix-turn");
  });

  it("is one of the two stream readers under src/services", () => {
    const files = readdirSync(join(SRC, "services")).filter((name) => name.endsWith(".ts"));
    const readers = files
      .filter((name) => {
        if (name === "admin-chat-sse.ts" || name === "ask-chat-sse.ts") return false;
        const text = readFileSync(join(SRC, "services", name), "utf-8");
        return /getReader\s*\(/.test(text);
      })
      .sort();
    // The admin chat and the Ask chat each own one reader, and `ask-chat-sse.ts`
    // pins its targets to `/api/ask/chat/...` in its own test.
    expect(readers).toEqual([]);
    expect(sep).toBeTruthy();
  });

  it("the app's chat route strings are the same-origin ones", () => {
    const service = readFileSync(join(SRC, "services", "admin-chat-service.ts"), "utf-8");
    expect(service).not.toMatch(/https?:\/\//);
    expect(service).not.toContain("8643");
  });
});
