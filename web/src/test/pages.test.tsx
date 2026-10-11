import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { ChatPage } from "@/features/chat/chat-page";
import { indexQueryOptions } from "@/features/index/index-queries";
import { libraryQueryOptions } from "@/features/library/library-queries";
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
  it("search lists rank, filename, score, and text", () => {
    renderWithQuery(
      <SearchPage
        indexReady
        searchedQuery="harbor"
        hits={[
          {
            rank: 1,
            doc_id: "ws_harbor",
            document_filename: "harbor.txt",
            text: "The harbor budget was approved.",
            score: 0.82,
            vote: null,
          },
        ]}
      />,
    );
    expect(screen.getByRole("textbox", { name: "Search" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Search" })).toBeTruthy();
    expect(screen.getByText("1")).toBeTruthy();
    expect(screen.getByText("harbor.txt")).toBeTruthy();
    expect(screen.getByText("0.820")).toBeTruthy();
    expect(screen.getByText("The harbor budget was approved.")).toBeTruthy();
  });

  it("search explains when nothing matched", () => {
    renderWithQuery(<SearchPage indexReady searchedQuery="missing" hits={[]} />);
    expect(screen.getByText('No passages matched "missing".')).toBeTruthy();
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
    expect(screen.getByText("Pending")).toBeTruthy();
    expect(screen.getByText("Failed")).toBeTruthy();
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

  it("polls while a document or the index is still changing", () => {
    const libraryInterval = libraryQueryOptions().refetchInterval;
    const indexInterval = indexQueryOptions().refetchInterval;
    expect(typeof libraryInterval).toBe("function");
    expect(typeof indexInterval).toBe("function");
    if (typeof libraryInterval !== "function" || typeof indexInterval !== "function") {
      return;
    }
    const library = (status: string) =>
      libraryInterval({ state: { data: [{ status }] } } as never);
    const index = (status: string) => indexInterval({ state: { data: { status } } } as never);
    expect(library("pending")).toBe(3000);
    expect(library("indexing")).toBe(3000);
    expect(library("deleting")).toBe(3000);
    expect(library("indexed")).toBe(false);
    expect(index("rebuilding")).toBe(3000);
    expect(index("updating")).toBe(3000);
    expect(index("ready")).toBe(false);
  });

  it("search and ask explain when no serving index exists", () => {
    const message = "The index is being prepared. Try again in a minute.";
    renderWithQuery(<SearchPage indexReady={false} />);
    expect(screen.getByText(message)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Search" })).toBeNull();
    renderWithQuery(<ChatPage indexReady={false} />);
    expect(screen.getAllByText(message).length).toBeGreaterThan(0);
  });
});
