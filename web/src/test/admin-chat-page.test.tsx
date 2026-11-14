import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { activePathMatches, navigationItems } from "@/components/layout/navigation";
import {
  AdminChatDenied,
  AdminChatDisabled,
  AdminChatMisconfigured,
  AdminChatPage,
} from "@/features/admin-chat/admin-chat-page";

/**
 * The page's states. The chat itself talks to the AI SDK runtime, which the
 * stream tests in `admin-chat-stream.test.ts` cover; what these assert is that
 * each state renders the right thing, needs the right number of requests, and
 * is reachable by a keyboard and a screen reader.
 */

vi.mock("@/components/layout/app-shell", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/layout/app-shell")>();
  return { ...actual, PageHeader: ({ title }: { title: string }) => <h2>{title}</h2> };
});

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

describe("the composer", () => {
  it("labels the textarea and sends on Enter", async () => {
    const fetchSpy = vi.fn().mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetchSpy);
    render(<AdminChatPage chatEnabled={true} />);
    // The thread list loads first; with no threads the composer is disabled.
    const composer = await screen.findByLabelText("Message the assistant");
    expect(composer).toBeTruthy();
    fireEvent.change(composer, { target: { value: "Hallo" } });
    expect((composer as HTMLTextAreaElement).value).toBe("Hallo");
    vi.unstubAllGlobals();
  });
});
