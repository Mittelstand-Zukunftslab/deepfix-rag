import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { ChatPage } from "@/features/chat/chat-page";
import { GalleryPage } from "@/features/gallery/gallery-page";
import { SearchPage } from "@/features/search/search-page";
import { UploadPage } from "@/features/upload/upload-page";

const renderWithQuery = (ui: ReactNode) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Suspense fallback={<p>Loading</p>}>{ui}</Suspense>
    </QueryClientProvider>,
  );
};

describe("Dashboard pages", () => {
  it("search shows ranked results and records feedback", async () => {
    renderWithQuery(<SearchPage />);
    expect(await screen.findByText("Northwind Power & Light: Electricity bill")).toBeTruthy();
    const [up] = screen.getAllByRole("button", { name: "Helpful" });
    if (!up) throw new Error("Missing feedback button");
    fireEvent.click(up);
    expect(up.getAttribute("aria-pressed")).toBe("true");
  });

  it("chat sends a message and receives a reply", async () => {
    renderWithQuery(<ChatPage />);
    const input = await screen.findByLabelText("Message");
    fireEvent.change(input, { target: { value: "Show water bills" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText("Show water bills")).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/I searched your archive/)).toBeTruthy(), {
      timeout: 3000,
    });
  });

  it("upload lists recent files", async () => {
    renderWithQuery(<UploadPage />);
    expect(await screen.findByText("4 files")).toBeTruthy();
  });

  it("gallery paginates 36 documents", async () => {
    renderWithQuery(<GalleryPage />);
    expect(await screen.findByText("36", { selector: "strong" })).toBeTruthy();
  });
});
