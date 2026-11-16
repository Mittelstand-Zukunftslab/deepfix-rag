import { describe, expect, it } from "vitest";

/**
 * The shell's scroll chain, asserted as source text.
 *
 * The layout defect that reached production was in this chain, not in a component:
 * the page clipped its own content below `lg`, so the shell's `<main>` — the real
 * scroll container — had nothing to scroll and the stacked sources could never be
 * reached. jsdom cannot measure any of it, and a harness that renders the page alone
 * cannot see it either, because the container is the shell.
 *
 * So this asserts the properties of the chain that make it work, by reading the two
 * files. It is a text test on purpose: the alternative is a browser in CI.
 */
describe("the authenticated layout's scroll chain", () => {
  const read = (path: string): Promise<string> =>
    import("node:fs/promises").then((fs) => fs.readFile(new URL(path, import.meta.url), "utf8"));

  it("is a fixed-height shell whose main scrolls", async () => {
    const shell = await read("../components/layout/app-shell.tsx");
    // `h-screen overflow-hidden` on the shell and `overflow-auto` on `<main>` are
    // what make `<main>` the one scroll container of the authenticated pages.
    expect(shell).toContain("flex h-screen flex-col overflow-hidden");
    expect(shell).toContain('id="content"');
    expect(shell).toMatch(/<main id="content" className="[^"]*overflow-auto/);
  });

  it("lets the Ask page grow below lg and clip only the side-by-side layout", async () => {
    const page = await read("../features/chat/chat-page.tsx");
    // Nothing in the page may clip its content unconditionally: below `lg` the page
    // has to grow so `<main>` can scroll to the folded sources.
    const unconditionalClips = page.match(/className="[^"]*\boverflow-hidden\b[^"]*"/g) ?? [];
    const offenders = unconditionalClips.filter((cls) => !cls.includes("lg:"));
    expect(offenders, "a page element clips its content on every viewport").toEqual([]);
    // And the page must not pin its own height below `lg` unless it is the `lg` rule.
    expect(page).not.toMatch(/min-h-\[calc\(100vh/);
    // The chat column keeps a floor so a short viewport still has a chat.
    expect(page).toContain("min-h-[19rem]");
    // The stacked sources cap themselves rather than taking a share of a short row.
    expect(page).toContain("max-h-72");
  });
});
