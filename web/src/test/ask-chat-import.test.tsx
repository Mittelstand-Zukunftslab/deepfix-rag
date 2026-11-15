import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AskPage, legacyStorageKeys, parseLegacySessions } from "@/features/chat/chat-page";
import { askChatService } from "@/services/ask-chat-service";

/**
 * The one-time import of the browser's pre-server conversations (FR-033).
 *
 * The page is the real one; the service is stubbed at its own boundary, and
 * localStorage is the real jsdom one, because the contract under test is exactly
 * "read these two keys once, then remove them".
 */

const THREAD = { thread_id: "t1", title: "Neu", created_at: "a", updated_at: "b" };

const renderPage = (ui: ReactNode) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Suspense fallback={<p>Loading</p>}>{ui}</Suspense>
    </QueryClientProvider>,
  );
};

const LEGACY = [
  {
    id: "session-1",
    title: "Alte Frage",
    createdAt: 1,
    updatedAt: 2,
    turns: [{ interactionId: "i-1", userQuery: "Was ist das?", answer: "Antwort." }],
  },
];

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
  window.localStorage.clear();
  vi.spyOn(askChatService, "listThreads").mockResolvedValue([THREAD] as never);
  vi.spyOn(askChatService, "getThread").mockResolvedValue({
    thread: THREAD,
    turns: [],
  } as never);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("data: [DONE]\n\n", { status: 200 })),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("the legacy import", () => {
  it("imports once, then removes both keys and never asks again", async () => {
    const keys = legacyStorageKeys("ada");
    window.localStorage.setItem(keys.sessions, JSON.stringify(LEGACY));
    window.localStorage.setItem(keys.active, "session-1");
    const importSessions = vi
      .spyOn(askChatService, "importSessions")
      .mockResolvedValue({ threadsCreated: 1, turnsCreated: 1, turnsSkipped: 0 });

    const first = renderPage(<AskPage indexReady ownerId="ada" />);
    await waitFor(() => expect(importSessions).toHaveBeenCalledTimes(1));
    expect(importSessions).toHaveBeenCalledWith([
      {
        id: "session-1",
        title: "Alte Frage",
        createdAt: 1,
        updatedAt: 2,
        turns: [{ interactionId: "i-1", userQuery: "Was ist das?", answer: "Antwort." }],
      },
    ]);
    // Both keys go, whatever the server answered, so nothing is left in two places.
    await waitFor(() => {
      expect(window.localStorage.getItem(keys.sessions)).toBeNull();
      expect(window.localStorage.getItem(keys.active)).toBeNull();
    });
    first.unmount();

    // A second visit finds the marker and the keys already gone: no request.
    renderPage(<AskPage indexReady ownerId="ada" />);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(importSessions).toHaveBeenCalledTimes(1);
  });

  it("makes no request at all for a member whose keys were already removed", async () => {
    const importSessions = vi.spyOn(askChatService, "importSessions");
    window.localStorage.setItem(legacyStorageKeys("ada").imported, "1");
    renderPage(<AskPage indexReady ownerId="ada" />);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(importSessions).not.toHaveBeenCalled();
  });

  it("removes the keys even when the import fails, and says so once", async () => {
    const keys = legacyStorageKeys("ada");
    window.localStorage.setItem(keys.sessions, JSON.stringify(LEGACY));
    vi.spyOn(askChatService, "importSessions").mockRejectedValue(
      new Error("Unable to reach the server."),
    );
    renderPage(<AskPage indexReady ownerId="ada" />);
    await waitFor(() => {
      expect(window.localStorage.getItem(keys.sessions)).toBeNull();
      expect(window.localStorage.getItem(keys.imported)).toBe("1");
    });
  });

  it("drops a legacy turn with no interaction id instead of sending an orphan", () => {
    const sessions = parseLegacySessions(
      JSON.stringify([
        {
          id: "s",
          title: "t",
          createdAt: 1,
          updatedAt: 1,
          turns: [
            { userQuery: "no id", answer: "x" },
            { interactionId: "i-2", userQuery: "has id", answer: "y" },
          ],
        },
      ]),
    );
    expect(sessions[0]?.turns).toEqual([
      { interactionId: "i-2", userQuery: "has id", answer: "y" },
    ]);
    // An unreadable key is not an error; there is simply nothing to import.
    expect(parseLegacySessions("{not json")).toEqual([]);
    expect(parseLegacySessions(null)).toEqual([]);
  });
});
