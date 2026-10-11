import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");
const GENERATED = join(SRC, "client");

const sourceFiles = (dir: string): string[] =>
  readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(name) ? [path] : [];
  });

// server.ts is the SSR request handler; its `fetch` is the server entry point, not an API call.
const EXEMPT = new Set(["server.ts", join("test", "api-boundary.test.ts")]);

const appFiles = sourceFiles(SRC).filter(
  (file) => !file.startsWith(GENERATED + sep) && !EXEMPT.has(relative(SRC, file)),
);

describe("API client boundary", () => {
  it("only the generated client calls fetch", () => {
    const offenders = appFiles.filter((file) => /\bfetch\s*\(/.test(readFileSync(file, "utf-8")));
    expect(offenders.map((file) => relative(SRC, file))).toEqual([]);
  });

  it("only src/api.ts and src/lib/config.ts know the API origin", () => {
    const allowed = new Set(["api.ts", join("lib", "config.ts")]);
    const offenders = appFiles
      .map((file) => relative(SRC, file))
      .filter((file) => !allowed.has(file))
      .filter((file) =>
        /apiBaseUrl|VITE_API_BASE_URL/.test(readFileSync(join(SRC, file), "utf-8")),
      );
    // Status copy may display the configured origin to the user, but must not build requests from it.
    expect(offenders).toEqual([join("features", "status", "status-page.tsx")]);
  });

  it("the generated SDK exposes no /internal operations", () => {
    const sdk = readFileSync(join(GENERATED, "sdk.gen.ts"), "utf-8");
    expect(sdk).not.toMatch(/url:\s*['"]\/internal/);
  });
});
