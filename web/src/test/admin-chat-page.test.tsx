import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { activePathMatches, navigationItems } from "@/components/layout/navigation";
import {
  AdminChatDenied,
  AdminChatDisabled,
  AdminChatMisconfigured,
  AdminChatPage,
} from "@/features/admin-chat/admin-chat-page";
import { groupThreadsByDate } from "@/features/admin-chat/thread-groups";
import type { ChatThreadSummaryResponse } from "@/client";
import { adminChatService } from "@/services/admin-chat-service";
import { useAdminChatStore } from "@/stores/admin-chat-store";

/**
 * The page's states and its shell. The chat itself talks to the AI SDK runtime,
 * which the stream tests in `admin-chat-stream.test.ts` cover; what these assert
 * is that each state renders the right thing, needs the right number of
 * requests, and is reachable by a keyboard and a screen reader.
 */

vi.mock("@/components/layout/app-shell", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/layout/app-shell")>();
  return { ...actual, PageHeader: ({ title }: { title: string }) => <h2>{title}</h2> };
});

function threadSummary(
  threadId: string,
  title: string,
  updatedAt: string,
): ChatThreadSummaryResponse {
  return {
    thread_id: threadId,
    title,
    created_at: updatedAt,
    updated_at: updatedAt,
    last_turn_status: "completed",
  };
}

const isoDaysAgo = (days: number) => {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString();
};

describe("the admin chat's disabled state", () => {
  it("says how to turn chat on and makes no chat request", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    render(<AdminChatPage chatEnabled={false} />);
    expect(
      screen.getByRole("heading", { name: "Hermes chat is turned off on this server." }),
    ).toBeTruthy();
    expect(screen.getByText(/HERMES_CHAT_ENABLED=true/)).toBeTruthy();
    expect(screen.queryByLabelText("Message the assistant")).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

describe("the admin chat's denied and misconfigured states", () => {
  it("shows a member the denied page with no composer", () => {
    render(<AdminChatDenied />);
    expect(screen.getByRole("heading", { name: "Hermes chat" })).toBeTruthy();
    expect(screen.getByText("This page is only available to admins.")).toBeTruthy();
    expect(screen.queryByLabelText("Message the assistant")).toBeNull();
  });

  it("names the missing variables when chat is on but broken", () => {
    render(
      <AdminChatMisconfigured message="set HERMES_CHAT_API_SERVER_KEY and HERMES_CHAT_MODEL" />,
    );
    const alert = screen.getByRole("alert");
    expect(within(alert).getByText(/HERMES_CHAT_API_SERVER_KEY/)).toBeTruthy();
    expect(alert.textContent).not.toContain("turned off");
  });
});

describe("the Admin navigation", () => {
  it("adds Hermes chat directly after Optimization, for an admin only", () => {
    const admin = navigationItems("admin").map((item) => item.to);
    expect(admin.indexOf("/admin/chat")).toBe(admin.indexOf("/admin") + 1);
    expect(navigationItems("member").map((item) => item.to)).not.toContain("/admin/chat");
    expect(navigationItems("member")).toHaveLength(navigationItems("admin").length - 3);
  });

  it("highlights exactly one item at /admin and at /admin/chat", () => {
    const items = navigationItems("admin");
    const atAdmin = items.filter((item) => activePathMatches("/admin", items, item.to));
    const atChat = items.filter((item) => activePathMatches("/admin/chat", items, item.to));
    expect(atAdmin.map((item) => item.to)).toEqual(["/admin"]);
    expect(atChat.map((item) => item.to)).toEqual(["/admin/chat"]);
  });

  it("does not treat a neighbouring path as a prefix match", () => {
    const items = navigationItems("admin");
    expect(activePathMatches("/adminx", items, "/admin")).toBe(false);
    expect(activePathMatches("/chat", items, "/chat")).toBe(true);
  });
});

describe("grouping the threads by when they were last used", () => {
  it("buckets Today, Yesterday, the previous week, and older, dropping empty buckets", () => {
    const groups = groupThreadsByDate([
      threadSummary("old", "Old", isoDaysAgo(30)),
      threadSummary("today", "Today thread", isoDaysAgo(0)),
      threadSummary("week", "Week", isoDaysAgo(3)),
      threadSummary("yesterday", "Yesterday thread", isoDaysAgo(1)),
    ]);
    expect(groups.map((group) => group.label)).toEqual([
      "Today",
      "Yesterday",
      "Previous 7 days",
      "Older",
    ]);
    expect(groups.flatMap((group) => group.threads.map((thread) => thread.title))).toEqual([
      "Today thread",
      "Yesterday thread",
      "Week",
      "Old",
    ]);
  });

  it("draws only the groups that hold a thread", () => {
    const groups = groupThreadsByDate([threadSummary("today", "Only", isoDaysAgo(0))]);
    expect(groups.map((group) => group.label)).toEqual(["Today"]);
  });
});

describe("the chat shell", () => {
  beforeEach(() => {
    useAdminChatStore.setState({ sidebarCollapsed: false });
    vi.spyOn(adminChatService, "threads").mockResolvedValue([
      threadSummary("a", "Champion question", isoDaysAgo(0)),
      threadSummary("b", "Older question", isoDaysAgo(20)),
    ]);
    vi.spyOn(adminChatService, "thread").mockRejectedValue(new Error("not needed"));
    vi.spyOn(adminChatService, "runs").mockResolvedValue([]);
  });

  it("shows the grouped threads, the active one, the header title, and the account", async () => {
    render(<AdminChatPage chatEnabled email="admin@example.com" />);

    expect(await screen.findByRole("heading", { name: "Today" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Older" })).toBeTruthy();
    expect(screen.getByText("admin@example.com")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();

    // The newest thread opens by default, and exactly one row is current.
    const rows = await screen.findAllByRole("button", { name: "Champion question" });
    expect(rows[0]!.getAttribute("aria-current")).toBe("true");
    expect(screen.getByRole("heading", { name: "Champion question" })).toBeTruthy();
    expect(screen.getByText("HERMES chat")).toBeTruthy();
    expect(screen.getAllByText("HERMES chat")).toHaveLength(1);
  });

  it("collapses the sidebar from the header and remembers the choice", async () => {
    render(<AdminChatPage chatEnabled />);
    const toggle = await screen.findByRole("button", { name: "Hide conversations" });
    fireEvent.click(toggle);
    expect(useAdminChatStore.getState().sidebarCollapsed).toBe(true);
    expect(screen.getByRole("button", { name: "Show conversations" })).toBeTruthy();
  });

  it("asks before deleting, then deletes the thread the dialog names", async () => {
    const remove = vi.spyOn(adminChatService, "deleteThread").mockResolvedValue({ deleted: true });
    render(<AdminChatPage chatEnabled />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete thread Champion question" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/who started it, not what was said/)).toBeTruthy();
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete thread" }));
    expect(remove).toHaveBeenCalledWith("a");
  });
});

describe("the composer", () => {
  it("labels the textarea and accepts text", async () => {
    const fetchSpy = vi.fn().mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetchSpy);
    vi.spyOn(adminChatService, "threads").mockResolvedValue([]);
    render(<AdminChatPage chatEnabled />);
    // The thread list loads first; with no threads the composer is disabled.
    const composer = await screen.findByLabelText("Message the assistant");
    expect(composer).toBeTruthy();
    fireEvent.change(composer, { target: { value: "Hallo" } });
    expect((composer as HTMLTextAreaElement).value).toBe("Hallo");
    vi.unstubAllGlobals();
  });
});
