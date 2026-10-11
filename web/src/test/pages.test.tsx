import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { ChatPage } from "@/features/chat/chat-page";
import { LibraryPage } from "@/features/library/library-page";
import { SearchPage } from "@/features/search/search-page";
import type { IndexStatus } from "@/services/index-service";
import type { LibraryDocument } from "@/services/library-service";

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

  it("library explains an empty corpus and marks a scanned pdf", () => {
    renderWithQuery(<LibraryPage documents={[]} />);
    expect(
      screen.getByText("No documents yet. Upload a text, Markdown, or PDF file to start."),
    ).toBeTruthy();
    expect(screen.getByLabelText("Upload a file")).toBeTruthy();
  });

  it("library shows scanned and garbled pages", () => {
    const documents: LibraryDocument[] = [
      {
        doc_id: "ws_scan",
        filename: "scan.pdf",
        media_type: "application/pdf",
        status: "failed",
        index_error: "No extractable text. This PDF looks scanned, and OCR is not available.",
        issues: [{ page: 1, kind: "scanned" }],
        uploader_email: "ada@example.com",
        created_at: "2026-10-11T12:00:00.000Z",
        can_delete: true,
      },
      {
        doc_id: "ws_cipher",
        filename: "cipher.pdf",
        media_type: "application/pdf",
        status: "pending",
        index_error: null,
        issues: [{ page: 2, kind: "garbled" }],
        uploader_email: "bea@example.com",
        created_at: "2026-10-11T12:05:00.000Z",
        can_delete: false,
      },
    ];
    renderWithQuery(<LibraryPage documents={documents} />);
    expect(screen.getByText("Page 1 looks scanned")).toBeTruthy();
    expect(
      screen.getByText("No extractable text. This PDF looks scanned, and OCR is not available."),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete scan.pdf" })).toBeTruthy();
    expect(screen.getByText("Page 2 has garbled text")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete cipher.pdf" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Rebuild index" })).toBeNull();
  });

  it("library lets an admin confirm a rebuild that keeps the current index", () => {
    const index: IndexStatus = {
      status: "ready",
      document_count: 2,
      chunk_count: 4,
      pending_document_count: 0,
      updated_at: "2026-10-11T12:00:00.000Z",
      config_path: "/tmp/champion.yaml",
      serving_build_id: "build-1",
      error: null,
    };
    renderWithQuery(<LibraryPage documents={[]} index={index} canRebuild />);
    expect(screen.getByText("Ready")).toBeTruthy();
    expect(screen.getByText("Chunks 4")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Rebuild index" }));
    expect(
      screen.getByText("The current index remains available until the new build is ready."),
    ).toBeTruthy();
  });

  it("search and ask explain when no serving index exists", () => {
    const message = "The index is being prepared. Try again in a minute.";
    renderWithQuery(<SearchPage indexReady={false} />);
    expect(screen.getByText(message)).toBeTruthy();
    expect(screen.queryByText("Northwind Power & Light: Electricity bill")).toBeNull();
    renderWithQuery(<ChatPage indexReady={false} />);
    expect(screen.getAllByText(message).length).toBeGreaterThan(0);
  });
});
